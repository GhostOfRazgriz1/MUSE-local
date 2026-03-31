import React, { useState, useEffect, useCallback } from "react";
import { IconFolderOpen, IconFileText, IconRefresh, IconChevronLeft, IconDownload } from "./Icons";
import { apiFetch } from "../hooks/useApiToken";

interface FileEntry {
  name: string;
  type: "file" | "dir";
  size: number | null;
  modified: string;
}

interface FileBrowserProps {
  onClose: () => void;
}

function formatSize(bytes: number | null): string {
  if (bytes == null) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export const FileBrowser: React.FC<FileBrowserProps> = ({ onClose }) => {
  const [entries, setEntries] = useState<FileEntry[]>([]);
  const [currentPath, setCurrentPath] = useState<string>("");
  const [parentPath, setParentPath] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchDir = useCallback(async (path?: string) => {
    setLoading(true);
    try {
      const qs = path ? `?path=${encodeURIComponent(path)}` : "";
      const res = await apiFetch(`/api/files/browse${qs}`);
      if (res.ok) {
        const data = await res.json();
        setEntries(data.entries || []);
        setCurrentPath(data.path || "");
        setParentPath(data.parent || null);
      }
    } catch {
      // silently fail
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchDir();
  }, [fetchDir]);

  const handleEntryClick = (entry: FileEntry) => {
    if (entry.type === "dir") {
      const newPath = currentPath + (currentPath.endsWith("/") || currentPath.endsWith("\\") ? "" : "/") + entry.name;
      fetchDir(newPath);
    } else {
      // Download file via authenticated fetch
      const filePath = currentPath + (currentPath.endsWith("/") || currentPath.endsWith("\\") ? "" : "/") + entry.name;
      apiFetch(`/api/files/download?path=${encodeURIComponent(filePath)}`)
        .then((res) => res.blob())
        .then((blob) => {
          const url = URL.createObjectURL(blob);
          const a = document.createElement("a");
          a.href = url;
          a.download = entry.name;
          a.click();
          URL.revokeObjectURL(url);
        })
        .catch(() => {});
    }
  };

  const dirName = currentPath.split(/[/\\]/).filter(Boolean).pop() || "MUSE";

  return (
    <div className="file-browser">
      <div className="file-browser-header">
        <div className="file-browser-title">
          <IconFolderOpen size={16} />
          <span>{dirName}</span>
        </div>
        <div className="file-browser-actions">
          <button
            className="btn btn-ghost btn-sm"
            onClick={() => fetchDir(currentPath)}
            title="Refresh"
          >
            <IconRefresh size={14} />
          </button>
          <button className="btn btn-ghost btn-sm" onClick={onClose}>
            Close
          </button>
        </div>
      </div>

      {/* Breadcrumb / back navigation */}
      {parentPath && (
        <button
          className="file-browser-back"
          onClick={() => fetchDir(parentPath)}
        >
          <IconChevronLeft size={14} />
          Back
        </button>
      )}

      <div className="file-browser-list">
        {loading && <div className="file-browser-loading">Loading...</div>}

        {!loading && entries.length === 0 && (
          <div className="file-browser-empty">
            <IconFolderOpen size={20} style={{ opacity: 0.3 }} />
            <span>Empty directory</span>
          </div>
        )}

        {!loading && entries.map((entry) => (
          <div
            key={entry.name}
            className={`file-browser-item ${entry.type}`}
            onClick={() => handleEntryClick(entry)}
          >
            <span className="file-browser-item-icon">
              {entry.type === "dir" ? <IconFolderOpen size={14} /> : <IconFileText size={14} />}
            </span>
            <span className="file-browser-item-name">{entry.name}</span>
            <span className="file-browser-item-size">{formatSize(entry.size)}</span>
            <span className="file-browser-item-date">{formatDate(entry.modified)}</span>
            {entry.type === "file" && (
              <button
                className="file-browser-item-dl"
                onClick={(e) => {
                  e.stopPropagation();
                  handleEntryClick(entry);
                }}
                title="Download"
              >
                <IconDownload size={12} />
              </button>
            )}
          </div>
        ))}
      </div>
    </div>
  );
};
