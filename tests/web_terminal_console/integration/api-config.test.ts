import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import { getConfig, getSkills } from '../../../src/web_terminal_client/src/api';
import { server } from '../mocks/server';

const BASE_URL = 'http://localhost:40080';
const TOKEN = 'valid-token';

describe('Config API', () => {
  describe('getConfig', () => {
    it('returns available models and default', async () => {
      const result = await getConfig(BASE_URL);

      expect(result.models_available).toBeDefined();
      expect(Array.isArray(result.models_available)).toBe(true);
      expect(result.models_available.length).toBeGreaterThan(0);
      expect(result.default_model).toBeTruthy();
    });

    it('includes expected models', async () => {
      const result = await getConfig(BASE_URL);

      expect(result.models_available).toContain('claude-3-sonnet');
      expect(result.models_available).toContain('claude-3-opus');
      expect(result.models_available).toContain('claude-3-haiku');
    });

    it('default model is in available models', async () => {
      const result = await getConfig(BASE_URL);

      expect(result.models_available).toContain(result.default_model);
    });

    it('handles server error', async () => {
      server.use(
        http.get(`${BASE_URL}/api/v1/config`, () => {
          return HttpResponse.json({ detail: 'Service unavailable' }, { status: 503 });
        })
      );

      await expect(getConfig(BASE_URL)).rejects.toThrow();
    });
  });
});

describe('Skills API', () => {
  describe('getSkills', () => {
    it('returns list of skills', async () => {
      const result = await getSkills(BASE_URL, TOKEN);

      expect(result.skills).toBeDefined();
      expect(Array.isArray(result.skills)).toBe(true);
      expect(result.skills.length).toBeGreaterThan(0);
    });

    it('each skill has required fields', async () => {
      const result = await getSkills(BASE_URL, TOKEN);

      result.skills.forEach((skill) => {
        expect(skill).toHaveProperty('id');
        expect(skill).toHaveProperty('name');
        expect(skill).toHaveProperty('description');
        expect(typeof skill.id).toBe('string');
        expect(typeof skill.name).toBe('string');
        expect(typeof skill.description).toBe('string');
      });
    });

    it('handles empty skills list', async () => {
      server.use(
        http.get(`${BASE_URL}/api/v1/skills`, () => {
          return HttpResponse.json({ skills: [] });
        })
      );

      const result = await getSkills(BASE_URL, TOKEN);

      expect(result.skills).toHaveLength(0);
    });

    it('requires authentication', async () => {
      server.use(
        http.get(`${BASE_URL}/api/v1/skills`, ({ request }) => {
          const authHeader = request.headers.get('Authorization');
          if (!authHeader || !authHeader.startsWith('Bearer ')) {
            return HttpResponse.json({ detail: 'Not authenticated' }, { status: 401 });
          }
          return HttpResponse.json({ skills: [] });
        })
      );

      // With valid token
      const result = await getSkills(BASE_URL, TOKEN);
      expect(result.skills).toBeDefined();
    });
  });
});
