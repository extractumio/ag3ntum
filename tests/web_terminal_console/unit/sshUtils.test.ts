/**
 * Tests for SSH utility functions.
 */
import { describe, expect, it } from 'vitest';
import { maskSSHKey, isEncryptedKey } from '../../../src/web_terminal_client/src/utils/sshUtils';

describe('maskSSHKey', () => {
  it('returns short keys unchanged', () => {
    const short = '-----BEGIN RSA PRIVATE KEY-----';
    expect(maskSSHKey(short)).toBe(short);
  });

  it('returns keys exactly 60 chars unchanged', () => {
    const key = 'A'.repeat(60);
    expect(maskSSHKey(key)).toBe(key);
  });

  it('masks long keys showing first 40 and last 20 chars', () => {
    const key = 'A'.repeat(40) + 'B'.repeat(20) + 'C'.repeat(20);
    const result = maskSSHKey(key);
    expect(result.startsWith('A'.repeat(40))).toBe(true);
    expect(result.endsWith('C'.repeat(20))).toBe(true);
    expect(result).toContain('********************');
  });

  it('produces a string shorter than original for long keys', () => {
    const key = '-----BEGIN RSA PRIVATE KEY-----\n' + 'x'.repeat(200) + '\n-----END RSA PRIVATE KEY-----';
    const masked = maskSSHKey(key);
    expect(masked.length).toBeLessThan(key.length);
  });
});

describe('isEncryptedKey', () => {
  it('returns true for encrypted key headers', () => {
    const encryptedKey = '-----BEGIN ENCRYPTED PRIVATE KEY-----\nMIIEpAIB...';
    expect(isEncryptedKey(encryptedKey)).toBe(true);
  });

  it('returns true for keys with Proc-Type ENCRYPTED in DEK info', () => {
    const oldStyleKey = '-----BEGIN RSA PRIVATE KEY-----\nProc-Type: 4,ENCRYPTED\nDEK-Info: AES...';
    expect(isEncryptedKey(oldStyleKey)).toBe(true);
  });

  it('returns false for unencrypted key', () => {
    const plainKey = '-----BEGIN RSA PRIVATE KEY-----\nMIIEo...';
    expect(isEncryptedKey(plainKey)).toBe(false);
  });

  it('returns false for empty string', () => {
    expect(isEncryptedKey('')).toBe(false);
  });
});
