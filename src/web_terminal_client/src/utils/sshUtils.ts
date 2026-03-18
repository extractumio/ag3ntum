/**
 * Utilities for SSH key display and detection.
 */

/**
 * Masks an SSH private key for safe display — shows first 40 chars,
 * asterisk padding, and last 20 chars.
 */
export function maskSSHKey(key: string): string {
  if (key.length <= 60) return key;
  return key.slice(0, 40) + '********************' + key.slice(-20);
}

/**
 * Returns true if the key header indicates it is passphrase-encrypted.
 */
export function isEncryptedKey(key: string): boolean {
  return key.includes('ENCRYPTED');
}
