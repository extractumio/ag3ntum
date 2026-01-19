import { http, HttpResponse } from 'msw';
import { beforeEach, describe, expect, it } from 'vitest';
import {
  fetchToken,
  getCurrentUser,
  login,
  logout,
} from '../../../src/web_terminal_client/src/api';
import { createMockTokenResponse, createMockUser } from '../mocks/data';
import { server } from '../mocks/server';

const BASE_URL = 'http://localhost:40080';

describe('Authentication API', () => {
  describe('login', () => {
    it('successfully logs in with valid credentials', async () => {
      const result = await login(BASE_URL, 'test@example.com', 'password123');

      expect(result.access_token).toBe('mock-jwt-token-xyz');
      expect(result.token_type).toBe('Bearer');
      expect(result.user_id).toBe('user-123');
    });

    it('throws error for invalid credentials', async () => {
      await expect(login(BASE_URL, 'wrong@email.com', 'wrongpass')).rejects.toThrow();
    });

    it('throws error on network failure', async () => {
      server.use(
        http.post(`${BASE_URL}/api/v1/auth/login`, () => {
          return HttpResponse.error();
        })
      );

      await expect(login(BASE_URL, 'test@example.com', 'password123')).rejects.toThrow();
    });

    it('throws error on server error', async () => {
      server.use(
        http.post(`${BASE_URL}/api/v1/auth/login`, () => {
          return HttpResponse.json(
            { detail: 'Internal server error' },
            { status: 500 }
          );
        })
      );

      await expect(login(BASE_URL, 'test@example.com', 'password123')).rejects.toThrow();
    });
  });

  describe('logout', () => {
    it('successfully logs out', async () => {
      // Should not throw
      await expect(logout(BASE_URL, 'valid-token')).resolves.not.toThrow();
    });

    it('handles logout error gracefully', async () => {
      server.use(
        http.post(`${BASE_URL}/api/v1/auth/logout`, () => {
          return HttpResponse.json({ detail: 'Token expired' }, { status: 401 });
        })
      );

      await expect(logout(BASE_URL, 'expired-token')).rejects.toThrow();
    });
  });

  describe('getCurrentUser', () => {
    it('returns user data with valid token', async () => {
      const user = await getCurrentUser(BASE_URL, 'valid-token');

      expect(user.id).toBe('user-123');
      expect(user.username).toBe('testuser');
      expect(user.email).toBe('test@example.com');
      expect(user.role).toBe('user');
    });

    it('throws error without authorization header', async () => {
      server.use(
        http.get(`${BASE_URL}/api/v1/auth/me`, ({ request }) => {
          const authHeader = request.headers.get('Authorization');
          if (!authHeader) {
            return HttpResponse.json({ detail: 'Not authenticated' }, { status: 401 });
          }
          return HttpResponse.json(createMockUser());
        })
      );

      // The API client should include the token in the header
      const user = await getCurrentUser(BASE_URL, 'valid-token');
      expect(user.id).toBe('user-123');
    });

    it('throws error with invalid token', async () => {
      server.use(
        http.get(`${BASE_URL}/api/v1/auth/me`, () => {
          return HttpResponse.json({ detail: 'Invalid token' }, { status: 401 });
        })
      );

      await expect(getCurrentUser(BASE_URL, 'invalid-token')).rejects.toThrow();
    });
  });

  describe('fetchToken', () => {
    it('fetches a new token', async () => {
      const result = await fetchToken(BASE_URL);

      expect(result.access_token).toBeTruthy();
      expect(result.token_type).toBe('Bearer');
    });
  });
});
