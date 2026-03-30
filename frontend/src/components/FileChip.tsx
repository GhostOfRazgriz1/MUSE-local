import React, { useState, useCallback } from "react";
import { IconFolderOpen, IconFileText, IconCheck, IconAlertCircle } from "./Icons";
import { apiFetch } from "../hooks/useApiToken";

/**
 * Regex to detect file paths in text.
 * Matches:
 *   - Windows:  C:\Users\foo\file.txt  or  C:/Users/foo/file.txt
 *   - Unix:     /home/user/file.txt  or  ~/documents/file.txt
 *   - Relative: ./output/result.json  or  data/output.csv
 * Requires at least one path separator and a filename with extension.
 */
const FILE_PATH_RE =
  /(?:[A-Za-z]:[\\\/]|[~.]?\/)[^\s<>"'`|?*\n]+\.\w{1,10}/g;

interface FileChipProps {
  path: string;
}

/** An inline chip that displays a file path with a "Show in folder" button. */
export const FileChip: React.FC<FileChipProps> = ({ path }) => {
  const [status, setStatus] = useState<"idle" | "ok" | "err">("idle");

  const handleReveal = useCallback(async (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    try {
      const res = await apiFetch("/api/files/reveal", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path }),
      });
      setStatus(res.ok ? "ok" : "err");
    } catch {
      setStatus("err");
    }
    setTimeout(() => setStatus("idle"), 2500);
  }, [path]);

  const filename = path.split(/[/\\]/).pop() || path;

  return (
    <span className="file-chip" title={path}>
      <IconFileText size={13} />
      <span className="file-chip-name">{filename}</span>
      <button
        className="file-chip-btn"
        onClick={handleReveal}
        aria-label={`Open folder for ${filename}`}
        title="Show in folder"
      >
        {status === "ok" ? (
          <IconCheck size={12} />
        ) : status === "err" ? (
          <IconAlertCircle size={12} />
        ) : (
          <IconFolderOpen size={12} />
        )}
      </button>
    </span>
  );
};

/**
 * Parse text and replace file paths with FileChip components.
 * Returns an array of strings and FileChip elements.
 */
export function renderWithFileChips(text: string): React.ReactNode[] {
  const parts: React.ReactNode[] = [];
  let lastIndex = 0;

  for (const match of text.matchAll(FILE_PATH_RE)) {
    const start = match.index!;
    if (start > lastIndex) {
      parts.push(text.slice(lastIndex, start));
    }
    parts.push(<FileChip key={start} path={match[0]} />);
    lastIndex = start + match[0].length;
  }

  if (lastIndex < text.length) {
    parts.push(text.slice(lastIndex));
  }

  return parts.length > 0 ? parts : [text];
}
