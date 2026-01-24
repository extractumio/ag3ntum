import React, { createContext, useContext, useEffect, useState } from 'react';
import { login as apiLogin, logout as apiLogout, getCurrentUser, invalidateAllCaches } from './api';
import { loadConfig } from './config';
import type { User } from './types';

interface AuthContextType {
  user: User | null;
  token: string | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  error: string | null;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [apiBaseUrl, setApiBaseUrl] = useState<string>('');

  useEffect(() => {
    // Load config first
    loadConfig().then((config) => {
      setApiBaseUrl(config.api.base_url);
      
      // Then check for stored token
      const storedToken = localStorage.getItem('auth_token');
      if (storedToken) {
        verifyToken(storedToken, config.api.base_url);
      } else {
        setIsLoading(false);
      }
    }).catch(() => {
      setIsLoading(false);
    });
  }, []);

  const verifyToken = async (token: string, baseUrl: string) => {
    try {
      const user = await getCurrentUser(baseUrl, token);
      setUser(user);
      setToken(token);
    } catch (err) {
      localStorage.removeItem('auth_token');
      setError('Session expired. Please login again.');
    } finally {
      setIsLoading(false);
    }
  };

  const login = async (email: string, password: string) => {
    setError(null);
    setIsLoading(true);

    try {
      const response = await apiLogin(apiBaseUrl, email, password);

      setToken(response.access_token);
      localStorage.setItem('auth_token', response.access_token);

      const user = await getCurrentUser(apiBaseUrl, response.access_token);
      setUser(user);

    } catch (err) {
      setError('Login failed. Check your credentials.');
      throw err;
    } finally {
      setIsLoading(false);
    }
  };

  const logout = async () => {
    try {
      if (token) {
        await apiLogout(apiBaseUrl, token);
      }
    } finally {
      // Clear all API caches on logout
      invalidateAllCaches();
      setUser(null);
      setToken(null);
      localStorage.removeItem('auth_token');
    }
  };

  return (
    <AuthContext.Provider
      value={{ user, token, isAuthenticated: !!token && !!user, isLoading, login, logout, error }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error('useAuth must be used within AuthProvider');
  return context;
}
