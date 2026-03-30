"""Agent OS entry point — wires all components and starts the server."""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from agent_os.config import Config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("agent_os")


async def create_orchestrator(config: Config | None = None):
    """Create and wire all components into an Orchestrator instance."""
    if config is None:
        config = Config()

    config.ensure_dirs()

    # Database initialization
    from agent_os.db.schema import init_agent_db, init_wal_db

    db = await init_agent_db(str(config.db_path))
    wal_db = await init_wal_db(str(config.wal_db_path))

    # Embedding service
    from agent_os.memory.embeddings import EmbeddingService

    embedding_service = EmbeddingService(config.memory.embedding_model)

    # Memory
    from agent_os.memory.repository import MemoryRepository
    from agent_os.memory.cache import MemoryCache
    from agent_os.memory.promotion import PromotionManager
    from agent_os.memory.demotion import DemotionManager

    memory_repo = MemoryRepository(db, embedding_service)
    memory_cache = MemoryCache(config.memory.cache_budget_mb)
    promotion_manager = PromotionManager(
        memory_repo, memory_cache, embedding_service,
        config.memory, config.registers,
    )
    demotion_manager = DemotionManager(memory_repo, memory_cache, embedding_service)

    # Permissions
    from agent_os.permissions.repository import PermissionRepository
    from agent_os.permissions.trust_budget import TrustBudgetManager
    from agent_os.permissions.manager import PermissionManager

    permission_repo = PermissionRepository(db)
    trust_budget = TrustBudgetManager(db)
    permission_manager = PermissionManager(permission_repo, trust_budget)

    # LLM Providers
    import os
    from agent_os.config import BUILTIN_PROVIDERS
    from agent_os.providers.openrouter import OpenRouterProvider
    from agent_os.providers.openai_compat import OpenAICompatibleProvider
    from agent_os.providers.anthropic import AnthropicProvider
    from agent_os.providers.registry import ProviderRegistry
    from agent_os.providers.model_router import ModelRouter

    # OpenRouter is the fallback for any model prefix without a direct provider.
    openrouter_key = os.environ.get("OPENROUTER_API_KEY", "")
    openrouter = OpenRouterProvider(
        api_key=openrouter_key, base_url=config.openrouter_base_url,
    )

    registry = ProviderRegistry(fallback=openrouter)

    # Register direct providers for every BUILTIN_PROVIDERS entry that has a key.
    for prefix, pdef in BUILTIN_PROVIDERS.items():
        if prefix == "openrouter":
            continue  # already the fallback
        key = os.environ.get(pdef.env_var, "")
        if not key:
            continue
        if pdef.api_style == "anthropic":
            registry.register(prefix, AnthropicProvider(api_key=key))
        else:
            registry.register(
                prefix,
                OpenAICompatibleProvider(
                    name=pdef.name,
                    api_key=key,
                    base_url=pdef.base_url,
                    env_var=pdef.env_var,
                ),
            )

    provider = registry
    model_router = ModelRouter(provider, db, config.default_model)

    # Credential vault
    from agent_os.credentials.vault import CredentialVault

    credential_vault = CredentialVault(db)

    # Sync env keys to vault so the UI can manage them.
    # Vault takes precedence once populated — env is just the bootstrap.
    if openrouter_key:
        existing = await credential_vault.retrieve_raw("openrouter_api_key")
        if not existing:
            await credential_vault.store(
                "openrouter_api_key", openrouter_key,
                credential_type="api_key", service_name="OpenRouter",
            )
            logger.info("Copied OPENROUTER_API_KEY from env to vault")
    else:
        stored_key = await credential_vault.retrieve("openrouter_api_key")
        if stored_key:
            openrouter._api_key = stored_key

    for prefix, pdef in BUILTIN_PROVIDERS.items():
        if prefix == "openrouter":
            continue
        # If we loaded from env, also persist to vault for UI management
        env_key = os.environ.get(pdef.env_var, "")
        if env_key and prefix in registry.providers:
            existing = await credential_vault.retrieve_raw(f"{prefix}_api_key")
            if not existing:
                await credential_vault.store(
                    f"{prefix}_api_key", env_key,
                    credential_type="api_key", service_name=pdef.name,
                )
            continue
        if prefix in registry.providers:
            continue
        stored_key = await credential_vault.retrieve(f"{prefix}_api_key")
        if not stored_key:
            continue
        if pdef.api_style == "anthropic":
            registry.register(prefix, AnthropicProvider(api_key=stored_key))
        else:
            registry.register(
                prefix,
                OpenAICompatibleProvider(
                    name=pdef.name,
                    api_key=stored_key,
                    base_url=pdef.base_url,
                    env_var=pdef.env_var,
                ),
            )

    # OAuth
    from agent_os.credentials.oauth import OAuthManager, PROVIDERS as OAUTH_PROVIDERS

    oauth_manager = OAuthManager(credential_vault, config, db=db)
    credential_vault.set_oauth_manager(oauth_manager)
    # Register domain → credential mappings for gateway injection
    for prov in OAUTH_PROVIDERS.values():
        for domain in prov.domains:
            credential_vault.register_domain(domain, prov.credential_id)

    # Audit
    from agent_os.audit.repository import AuditRepository

    audit_repo = AuditRepository(db)

    # WAL
    from agent_os.wal.log import WriteAheadLog

    wal = WriteAheadLog(wal_db)

    # Skills
    from agent_os.skills.loader import SkillLoader
    from agent_os.skills.sandbox import SkillSandbox
    from agent_os.skills.warm_pool import WarmPool

    skill_loader = SkillLoader(db, config.skills_dir)
    warm_pool = WarmPool(
        pool_size=config.execution.warm_pool_size_standard,
        max_reuse=config.execution.max_reuse_cycles,
    )
    skill_sandbox = SkillSandbox(config.skills_dir, config.ipc_dir, warm_pool)

    # API Gateway
    from agent_os.gateway.proxy import APIGateway

    gateway = APIGateway(credential_vault, audit_repo, config.gateway)

    # Load first-party skills
    builtin_skills = Path(__file__).parent.parent.parent / "skills"
    if builtin_skills.exists():
        await skill_loader.load_first_party_skills(builtin_skills)

    # Orchestrator
    from agent_os.kernel.orchestrator import Orchestrator

    orchestrator = Orchestrator(
        config=config,
        db=db,
        wal_db=wal_db,
        memory_repo=memory_repo,
        memory_cache=memory_cache,
        embedding_service=embedding_service,
        promotion_manager=promotion_manager,
        demotion_manager=demotion_manager,
        permission_manager=permission_manager,
        trust_budget=trust_budget,
        provider=provider,
        model_router=model_router,
        credential_vault=credential_vault,
        audit_repo=audit_repo,
        wal=wal,
        skill_loader=skill_loader,
        skill_sandbox=skill_sandbox,
        gateway=gateway,
        oauth_manager=oauth_manager,
    )

    return orchestrator


def main():
    """CLI entry point."""
    import argparse
    import uvicorn
    from agent_os.api.app import create_app

    parser = argparse.ArgumentParser(description="Agent OS")
    parser.add_argument("--debug", action="store_true",
                        help="Enable debug tracing (logs to data_dir/logs/)")
    args = parser.parse_args()

    config = Config(debug=args.debug)
    config.ensure_dirs()

    # Initialize debug tracer
    from agent_os.debug import DebugTracer, set_tracer
    tracer = DebugTracer(enabled=config.debug, logs_dir=config.logs_dir)
    set_tracer(tracer)

    if config.debug:
        logging.getLogger().setLevel(logging.DEBUG)
        logger.info("Debug mode ON — logs at %s", config.logs_dir)

    app = create_app()

    logger.info(f"Starting Agent OS on http://{config.server.host}:{config.server.port}")
    uvicorn.run(
        app,
        host=config.server.host,
        port=config.server.port,
        log_level="debug" if config.debug else "info",
    )


if __name__ == "__main__":
    main()
