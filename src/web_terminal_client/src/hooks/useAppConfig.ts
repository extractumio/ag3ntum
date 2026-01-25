/**
 * useAppConfig Hook
 *
 * Manages application configuration including:
 * - Loading app config from YAML
 * - Loading API config (available models)
 * - Model selection with persistence
 * - Loading available skills
 */

import { useCallback, useEffect, useState } from 'react';
import { getConfig, getSkillsCached } from '../api';
import { loadConfig } from '../config';
import type { AppConfig, SkillInfo } from '../types';

// Local storage helpers
function getStoredSelectedModel(): string | null {
  try {
    return localStorage.getItem('selectedModel');
  } catch {
    return null;
  }
}

function setStoredSelectedModel(model: string): void {
  try {
    localStorage.setItem('selectedModel', model);
  } catch {
    // Ignore storage errors
  }
}

export interface UseAppConfigResult {
  config: AppConfig | null;
  availableModels: string[];
  selectedModel: string;
  setSelectedModel: (model: string) => void;
  loadedSkills: SkillInfo[];
  isConfigLoading: boolean;
}

export function useAppConfig(token: string | null): UseAppConfigResult {
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [availableModels, setAvailableModels] = useState<string[]>([]);
  const [selectedModel, setSelectedModelState] = useState<string>('');
  const [loadedSkills, setLoadedSkills] = useState<SkillInfo[]>([]);
  const [isConfigLoading, setIsConfigLoading] = useState(true);

  // Load app config on mount
  useEffect(() => {
    setIsConfigLoading(true);
    loadConfig()
      .then(setConfig)
      .catch(() => setConfig(null))
      .finally(() => setIsConfigLoading(false));
  }, []);

  // Load available models from API config
  useEffect(() => {
    if (!config) {
      return;
    }
    getConfig(config.api.base_url)
      .then((apiConfig) => {
        setAvailableModels(apiConfig.models_available);
        // Check for stored model preference
        const storedModel = getStoredSelectedModel();
        if (storedModel && apiConfig.models_available.includes(storedModel)) {
          // Use stored model if it's still available
          setSelectedModelState(storedModel);
        } else {
          // Fall back to default model
          setSelectedModelState(apiConfig.default_model);
        }
      })
      .catch((err) => {
        console.error('Failed to load API config:', err);
      });
  }, [config]);

  // Load available skills
  useEffect(() => {
    if (!config || !token) {
      return;
    }
    getSkillsCached(config.api.base_url, token)
      .then((response: { skills: SkillInfo[] }) => {
        setLoadedSkills(response.skills);
      })
      .catch((err: Error) => {
        console.error('Failed to load skills:', err);
      });
  }, [config, token]);

  // Set selected model with persistence
  const setSelectedModel = useCallback((model: string) => {
    setSelectedModelState(model);
    setStoredSelectedModel(model);
  }, []);

  return {
    config,
    availableModels,
    selectedModel,
    setSelectedModel,
    loadedSkills,
    isConfigLoading,
  };
}
