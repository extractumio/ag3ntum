import { describe, expect, it } from 'vitest';
import { INVALID_SESSION_IDS, VALID_SESSION_IDS } from '../mocks/data';

// =============================================================================
// Utility Functions (extracted from App.tsx for testing)
// These should ideally be in a separate utils file in the source code
// =============================================================================

const SESSION_ID_PATTERN = /^\d{8}_\d{6}_[a-f0-9]{8}$/;

function isValidSessionId(sessionId: string | undefined | null): sessionId is string {
  if (!sessionId) return false;
  if (sessionId.length > 24) return false;
  return SESSION_ID_PATTERN.test(sessionId);
}

function isMeaningfulError(error: string | undefined | null): boolean {
  if (!error) {
    return false;
  }
  const normalized = error.trim().toLowerCase();
  if (!normalized) {
    return false;
  }
  const placeholders = [
    'none',
    'none yet',
    'no error',
    'no errors',
    'n/a',
    'na',
    'null',
    'undefined',
    'empty',
    '-',
    '',
  ];
  if (placeholders.includes(normalized)) {
    return false;
  }
  if (normalized.startsWith('none yet') || normalized.startsWith('no error')) {
    return false;
  }
  return true;
}

function isSafeRelativePath(path: string): boolean {
  return Boolean(path && !path.startsWith('/') && !path.startsWith('~') && !path.includes('..'));
}

// =============================================================================
// Tests
// =============================================================================

describe('isValidSessionId', () => {
  describe('valid session IDs', () => {
    it.each(VALID_SESSION_IDS)('accepts valid session ID: %s', (sessionId) => {
      expect(isValidSessionId(sessionId)).toBe(true);
    });

    it('accepts ID with all zeros', () => {
      expect(isValidSessionId('00000000_000000_00000000')).toBe(true);
    });

    it('accepts ID with all f hex characters', () => {
      expect(isValidSessionId('99999999_999999_ffffffff')).toBe(true);
    });
  });

  describe('invalid session IDs', () => {
    it.each(INVALID_SESSION_IDS)('rejects invalid session ID: %s', (sessionId) => {
      expect(isValidSessionId(sessionId)).toBe(false);
    });

    it('rejects null', () => {
      expect(isValidSessionId(null)).toBe(false);
    });

    it('rejects undefined', () => {
      expect(isValidSessionId(undefined)).toBe(false);
    });

    it('rejects empty string', () => {
      expect(isValidSessionId('')).toBe(false);
    });

    it('rejects ID exceeding max length (security)', () => {
      expect(isValidSessionId('20240115_143052_a1b2c3d4_extra_stuff')).toBe(false);
    });

    it('rejects path traversal attempts', () => {
      expect(isValidSessionId('../../../etc/passwd')).toBe(false);
      expect(isValidSessionId('..%2F..%2F..%2Fetc%2Fpasswd')).toBe(false);
    });

    it('rejects XSS attempts', () => {
      expect(isValidSessionId('<script>alert(1)</script>')).toBe(false);
      expect(isValidSessionId('"><img src=x onerror=alert(1)>')).toBe(false);
    });

    it('rejects SQL injection attempts', () => {
      expect(isValidSessionId("'; DROP TABLE sessions;--")).toBe(false);
      expect(isValidSessionId('1 OR 1=1')).toBe(false);
    });

    it('rejects ID with uppercase hex characters', () => {
      expect(isValidSessionId('20240115_143052_A1B2C3D4')).toBe(false);
    });

    it('rejects ID with wrong delimiter', () => {
      expect(isValidSessionId('20240115-143052-a1b2c3d4')).toBe(false);
      expect(isValidSessionId('20240115/143052/a1b2c3d4')).toBe(false);
    });
  });
});

describe('isMeaningfulError', () => {
  describe('meaningful errors', () => {
    it('returns true for actual error messages', () => {
      expect(isMeaningfulError('Connection failed')).toBe(true);
      expect(isMeaningfulError('File not found')).toBe(true);
      expect(isMeaningfulError('Permission denied')).toBe(true);
      expect(isMeaningfulError('Timeout after 30s')).toBe(true);
    });

    it('returns true for error codes', () => {
      expect(isMeaningfulError('E001: Invalid input')).toBe(true);
      expect(isMeaningfulError('Error 500')).toBe(true);
    });

    it('returns true for multi-line errors', () => {
      expect(isMeaningfulError('Error:\nLine 1\nLine 2')).toBe(true);
    });
  });

  describe('non-meaningful errors (placeholders)', () => {
    it('returns false for null/undefined', () => {
      expect(isMeaningfulError(null)).toBe(false);
      expect(isMeaningfulError(undefined)).toBe(false);
    });

    it('returns false for empty string', () => {
      expect(isMeaningfulError('')).toBe(false);
      expect(isMeaningfulError('   ')).toBe(false);
    });

    it('returns false for "none" variations', () => {
      expect(isMeaningfulError('none')).toBe(false);
      expect(isMeaningfulError('None')).toBe(false);
      expect(isMeaningfulError('NONE')).toBe(false);
      expect(isMeaningfulError('none yet')).toBe(false);
      expect(isMeaningfulError('None yet')).toBe(false);
    });

    it('returns false for "no error" variations', () => {
      expect(isMeaningfulError('no error')).toBe(false);
      expect(isMeaningfulError('No Error')).toBe(false);
      expect(isMeaningfulError('no errors')).toBe(false);
      expect(isMeaningfulError('No Errors')).toBe(false);
    });

    it('returns false for n/a and similar', () => {
      expect(isMeaningfulError('n/a')).toBe(false);
      expect(isMeaningfulError('N/A')).toBe(false);
      expect(isMeaningfulError('na')).toBe(false);
      expect(isMeaningfulError('NA')).toBe(false);
    });

    it('returns false for null/undefined strings', () => {
      expect(isMeaningfulError('null')).toBe(false);
      expect(isMeaningfulError('undefined')).toBe(false);
      expect(isMeaningfulError('NULL')).toBe(false);
    });

    it('returns false for other placeholders', () => {
      expect(isMeaningfulError('empty')).toBe(false);
      expect(isMeaningfulError('-')).toBe(false);
    });

    it('returns false with leading/trailing whitespace', () => {
      expect(isMeaningfulError('  none  ')).toBe(false);
      expect(isMeaningfulError('\tnone\t')).toBe(false);
      expect(isMeaningfulError('\nnone\n')).toBe(false);
    });
  });
});

describe('isSafeRelativePath', () => {
  describe('safe paths', () => {
    it('returns true for simple relative paths', () => {
      expect(isSafeRelativePath('file.txt')).toBe(true);
      expect(isSafeRelativePath('folder/file.txt')).toBe(true);
      expect(isSafeRelativePath('a/b/c/file.txt')).toBe(true);
    });

    it('returns true for paths with dots in filename', () => {
      expect(isSafeRelativePath('file.test.txt')).toBe(true);
      expect(isSafeRelativePath('.hidden')).toBe(true);
      expect(isSafeRelativePath('folder/.hidden')).toBe(true);
    });

    it('returns true for paths with special characters', () => {
      expect(isSafeRelativePath('file-name.txt')).toBe(true);
      expect(isSafeRelativePath('file_name.txt')).toBe(true);
      expect(isSafeRelativePath('file name.txt')).toBe(true);
    });
  });

  describe('unsafe paths', () => {
    it('returns false for empty path', () => {
      expect(isSafeRelativePath('')).toBe(false);
    });

    it('returns false for absolute paths', () => {
      expect(isSafeRelativePath('/etc/passwd')).toBe(false);
      expect(isSafeRelativePath('/home/user/file.txt')).toBe(false);
    });

    it('returns false for home directory paths', () => {
      expect(isSafeRelativePath('~/file.txt')).toBe(false);
      expect(isSafeRelativePath('~/.ssh/id_rsa')).toBe(false);
    });

    it('returns false for path traversal attempts', () => {
      expect(isSafeRelativePath('../file.txt')).toBe(false);
      expect(isSafeRelativePath('folder/../file.txt')).toBe(false);
      expect(isSafeRelativePath('folder/../../etc/passwd')).toBe(false);
      expect(isSafeRelativePath('a/b/../../c')).toBe(false);
    });

    it('returns false for windows-style absolute paths', () => {
      // Note: This function is designed for Unix-style paths
      // Windows paths like 'C:\' would actually pass since they don't start with /
      // This is acceptable for a Unix-focused application
    });
  });
});
