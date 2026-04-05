# MUSE

A local-first AI agent platform with persistent memory, modular skills, and customizable personality.

MUSE runs on your machine with local LLMs via Ollama, vLLM, or llama.cpp. No cloud services required. Your data stays on your device. It learns your preferences over time, executes tasks through skills, and connects to external tools via MCP.

## Features

- **Runs locally** -- Ollama, vLLM, or llama.cpp. No API keys, no cloud, no tracking
- **Persistent memory** -- three-tier system (registers, cache, disk) with semantic search
- **Skills** -- search the web, manage files, read documents, set reminders, run code, and more
- **MCP support** -- connect any MCP server (stdio, SSE, streamable-HTTP) to extend capabilities
- **Proactive behavior** -- adaptive greetings, suggestions, and autonomous actions gated by relationship level
- **Multi-task execution** -- compound requests decomposed into parallel/sequential sub-tasks with steering
- **Permission system** -- trust budgets and approval modes (always/session/once) for every action
- **Customizable identity** -- 3-step onboarding (name, agent name, personality) creates a unique character
- **Memory consolidation** -- "dreaming" extracts durable knowledge during idle time
- **Document Q&A** -- read, search, and summarize your local files

## Quick Start

**Prerequisites:** Python 3.12+, Node.js 18+, [Ollama](https://ollama.com) with at least one model pulled

```bash
# Pull a model first
ollama pull llama3.2

# Windows
start.bat

# macOS / Linux
chmod +x start.sh && ./start.sh
```

This will:
1. Create a Python virtual environment and install dependencies
2. Install frontend dependencies
3. Start the backend (HTTPS on port 8080) and frontend dev server (port 3000)
4. Open the browser

On first launch, you'll configure your local LLM server and go through a short identity setup.

## Architecture

```
muse/
  src/muse/
    kernel/              # Kernel, classifier, scheduler, dreaming
      orchestrator.py    # Kernel (thin dispatch layer)
      service_registry.py # Dependency injection container
      message_bus.py     # Async event pub/sub
      session_store.py   # Session state management
      intent_classifier.py
      compaction.py      # Conversation history compression
      dreaming.py        # Memory consolidation
      proactivity.py     # Suggestions, nudges, autonomous actions
      inline_handler.py  # Direct LLM responses
      mood.py            # Mood state machine
    api/routes/          # WebSocket + REST endpoints
    skills/              # Skill loader, sandbox, warm pool
    memory/              # Repository, cache, promotion, demotion
    permissions/         # Permission manager, trust budget
    mcp/                 # MCP client (stdio, SSE, streamable-HTTP)
    providers/           # Local LLM provider (Ollama/vLLM/llama.cpp)
    db/                  # SQLite schema
  sdk/muse_sdk/          # Python SDK for skill development
  skills/                # Built-in skills (10)
  frontend/src/          # React + TypeScript UI (Vite)
  tests/                 # Unit + integration tests (67 tests)
```

## Built-in Skills

| Skill | What it does |
|-------|-------------|
| **Files** | Read, write, edit, copy, move, search files |
| **Documents** | Q&A, search, and summarize local documents |
| **Notes** | Personal note-taking with semantic search |
| **Search** | Web search via Tavily, Brave, Bing, or DuckDuckGo |
| **Reminders** | Scheduled reminders with notifications |
| **Code Runner** | Execute Python code |
| **Shell** | Run shell commands |
| **Webpage Reader** | Fetch and summarize web pages |
| **Notify** | Desktop notifications |

## MCP Servers

Connect external tools in **Settings > MCP Servers**. Supports stdio, SSE, and streamable-HTTP transports. MUSE discovers tools automatically and routes to them alongside built-in skills.

Tested with: `mcp-server-time`, `mcp-server-sqlite`, `@modelcontextprotocol/server-filesystem`, `@modelcontextprotocol/server-everything`.

## Configuration

| Setting | Where |
|---------|-------|
| Local LLM server | Setup card on first launch, or Settings > Models |
| Workspace directory | Settings > General (default: `~/Documents/MUSE`) |
| Permissions | Settings > Security |
| Identity/personality | Editable via chat ("change your name to X") |
| MCP servers | Settings > MCP Servers |

## Data Storage

All data stays local:

| What | Where |
|------|-------|
| Database, identity, skills | `%LOCALAPPDATA%/muse/` (Windows), `~/Library/Application Support/muse/` (macOS), `~/.local/share/muse/` (Linux) |
| Agent workspace | `~/Documents/MUSE` |

No API keys stored. No telemetry. No cloud.

## Development

```bash
# Run unit tests
python -m pytest tests/test_service_registry.py tests/test_message_bus.py tests/test_session_store.py -v

# Run comprehensive integration test (requires running server + Ollama)
python tests/test_comprehensive.py

# Run MCP server tests (requires running server + Node.js)
python tests/test_mcp_servers.py

# Reset to fresh install
python reset_data.py
```

## License

MIT
