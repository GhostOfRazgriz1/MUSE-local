import React from "react";
import type { SessionUsage } from "../types/events";

interface UsageDashboardProps {
  usage: SessionUsage;
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

export const CostDashboard: React.FC<UsageDashboardProps> = ({ usage }) => {
  const total = usage.tokens_in + usage.tokens_out;
  return (
    <div className="cost-dashboard">
      <div className="cost-item">
        <span className="cost-label">In</span>
        <span className="cost-value">{formatTokens(usage.tokens_in)}</span>
      </div>
      <div className="cost-item">
        <span className="cost-label">Out</span>
        <span className="cost-value">{formatTokens(usage.tokens_out)}</span>
      </div>
      <div className="cost-item">
        <span className="cost-label">Total</span>
        <span className="cost-value highlight">{formatTokens(total)}</span>
      </div>
    </div>
  );
};
