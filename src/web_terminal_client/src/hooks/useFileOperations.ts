/**
 * useFileOperations Hook
 *
 * Manages file-related operations including:
 * - File viewer modal state
 * - File content loading
 * - File downloads
 * - File attachments for input
 */

import { useCallback, useState } from 'react';
import { downloadFile, getFileContent, getFileDownloadUrl } from '../api';
import { toFileViewerData, type FileViewerData } from '../FileViewer';
import type { AppConfig } from '../types';

export interface AttachedFile {
  file: File;
  preview?: string;
}

export interface UseFileOperationsResult {
  // File viewer state
  viewerFile: FileViewerData | null;
  viewerLoading: boolean;
  viewerImageUrl: string | undefined;
  openFileViewer: (path: string) => Promise<void>;
  closeFileViewer: () => void;
  // File attachments
  attachedFiles: AttachedFile[];
  setAttachedFiles: React.Dispatch<React.SetStateAction<AttachedFile[]>>;
  addAttachedFiles: (files: File[]) => void;
  removeAttachedFile: (index: number) => void;
  clearAttachedFiles: () => void;
  // File downloads
  handleDownloadFile: (path: string) => Promise<void>;
  // File explorer state
  fileExplorerVisible: boolean;
  setFileExplorerVisible: React.Dispatch<React.SetStateAction<boolean>>;
  fileExplorerRefreshKey: number;
  refreshFileExplorer: () => void;
  fileExplorerModalOpen: boolean;
  setFileExplorerModalOpen: React.Dispatch<React.SetStateAction<boolean>>;
  showHiddenFiles: boolean;
  setShowHiddenFiles: React.Dispatch<React.SetStateAction<boolean>>;
  navigateToPath: string | null;
  setNavigateToPath: React.Dispatch<React.SetStateAction<string | null>>;
}

export function useFileOperations(
  config: AppConfig | null,
  token: string | null,
  sessionId: string | undefined
): UseFileOperationsResult {
  // File viewer state
  const [viewerFile, setViewerFile] = useState<FileViewerData | null>(null);
  const [viewerLoading, setViewerLoading] = useState(false);
  const [viewerImageUrl, setViewerImageUrl] = useState<string | undefined>(undefined);

  // File attachments
  const [attachedFiles, setAttachedFiles] = useState<AttachedFile[]>([]);

  // File explorer state
  const [fileExplorerVisible, setFileExplorerVisible] = useState(false);
  const [fileExplorerRefreshKey, setFileExplorerRefreshKey] = useState(0);
  const [fileExplorerModalOpen, setFileExplorerModalOpen] = useState(false);
  const [showHiddenFiles, setShowHiddenFiles] = useState(false);
  const [navigateToPath, setNavigateToPath] = useState<string | null>(null);

  // Open file viewer with content
  const openFileViewer = useCallback(
    async (path: string) => {
      if (!config || !token || !sessionId) {
        return;
      }

      setViewerLoading(true);
      setViewerFile(null);
      setViewerImageUrl(undefined);

      try {
        const response = await getFileContent(config.api.base_url, token, sessionId, path);
        const fileData = toFileViewerData(response);
        setViewerFile(fileData);

        // If it's an image, also get the download URL for display
        if (fileData.mimeType?.startsWith('image/')) {
          const imageUrl = getFileDownloadUrl(config.api.base_url, token, sessionId, path);
          setViewerImageUrl(imageUrl);
        }
      } catch (err) {
        console.error('Failed to load file:', err);
        setViewerFile({
          name: path.split('/').pop() ?? 'unknown',
          path,
          content: null,
          mimeType: 'application/octet-stream',
          size: 0,
          error: (err as Error).message,
        });
      } finally {
        setViewerLoading(false);
      }
    },
    [config, token, sessionId]
  );

  // Close file viewer
  const closeFileViewer = useCallback(() => {
    setViewerFile(null);
    setViewerLoading(false);
    setViewerImageUrl(undefined);
  }, []);

  // Download file
  const handleDownloadFile = useCallback(
    async (path: string) => {
      if (!config || !token || !sessionId) {
        return;
      }

      try {
        await downloadFile(config.api.base_url, token, sessionId, path);
      } catch (err) {
        console.error('Failed to download file:', err);
      }
    },
    [config, token, sessionId]
  );

  // Add attached files
  const addAttachedFiles = useCallback((files: File[]) => {
    setAttachedFiles((prev) => [
      ...prev,
      ...files.map((file) => ({ file })),
    ]);
  }, []);

  // Remove attached file by index
  const removeAttachedFile = useCallback((index: number) => {
    setAttachedFiles((prev) => prev.filter((_, i) => i !== index));
  }, []);

  // Clear all attached files
  const clearAttachedFiles = useCallback(() => {
    setAttachedFiles([]);
  }, []);

  // Refresh file explorer
  const refreshFileExplorer = useCallback(() => {
    setFileExplorerRefreshKey((prev) => prev + 1);
  }, []);

  return {
    // File viewer
    viewerFile,
    viewerLoading,
    viewerImageUrl,
    openFileViewer,
    closeFileViewer,
    // File attachments
    attachedFiles,
    setAttachedFiles,
    addAttachedFiles,
    removeAttachedFile,
    clearAttachedFiles,
    // File downloads
    handleDownloadFile,
    // File explorer
    fileExplorerVisible,
    setFileExplorerVisible,
    fileExplorerRefreshKey,
    refreshFileExplorer,
    fileExplorerModalOpen,
    setFileExplorerModalOpen,
    showHiddenFiles,
    setShowHiddenFiles,
    navigateToPath,
    setNavigateToPath,
  };
}
