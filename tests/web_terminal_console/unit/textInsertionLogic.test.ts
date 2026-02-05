/**
 * Tests for text insertion logic utility
 *
 * Tests the pure logic for cursor-aware text insertion with smart spacing.
 */
import { describe, expect, it } from 'vitest';
import { calculateInsertText } from '../../../src/web_terminal_client/src/utils';

describe('calculateInsertText', () => {
  // ==========================================================================
  // Basic Insertion (no spacing needed)
  // ==========================================================================
  describe('Basic Insertion', () => {
    it('inserts text into empty string', () => {
      const result = calculateInsertText('', 0, 0, 'hello');
      expect(result.newValue).toBe('hello');
      expect(result.newCursorPos).toBe(5);
    });

    it('inserts at cursor position in middle of text', () => {
      // "Hello World" with cursor after "Hello "
      const result = calculateInsertText('Hello World', 6, 6, 'Beautiful');
      // Should become "Hello Beautiful World" (space after "Hello" already exists)
      expect(result.newValue).toBe('Hello Beautiful World');
    });

    it('appends text at end after space', () => {
      const result = calculateInsertText('Hello ', 6, 6, 'World');
      expect(result.newValue).toBe('Hello World');
      expect(result.newCursorPos).toBe(11);
    });

    it('prepends text at start before space', () => {
      const result = calculateInsertText(' World', 0, 0, 'Hello');
      expect(result.newValue).toBe('Hello World');
      expect(result.newCursorPos).toBe(5);
    });
  });

  // ==========================================================================
  // Smart Spacing — Space Before
  // ==========================================================================
  describe('Smart Spacing - Space Before', () => {
    it('adds space before when cursor follows non-whitespace', () => {
      // "Hello" with cursor at end (position 5)
      const result = calculateInsertText('Hello', 5, 5, 'World');
      expect(result.paddedText).toBe(' World');
      expect(result.newValue).toBe('Hello World');
    });

    it('does not add space before when cursor follows whitespace', () => {
      const result = calculateInsertText('Hello ', 6, 6, 'World');
      expect(result.paddedText).toBe('World');
      expect(result.newValue).toBe('Hello World');
    });

    it('does not add space before when cursor is at start', () => {
      const result = calculateInsertText('World', 0, 0, 'Hello');
      expect(result.paddedText).toBe('Hello ');
      expect(result.newValue).toBe('Hello World');
    });

    it('does not add space before when preceding char is newline', () => {
      const result = calculateInsertText('Hello\n', 6, 6, 'World');
      expect(result.paddedText).toBe('World');
    });

    it('does not add space before when preceding char is tab', () => {
      const result = calculateInsertText('Hello\t', 6, 6, 'World');
      expect(result.paddedText).toBe('World');
    });
  });

  // ==========================================================================
  // Smart Spacing — Space After
  // ==========================================================================
  describe('Smart Spacing - Space After', () => {
    it('adds space after when cursor precedes non-whitespace', () => {
      // "World" with cursor at start (position 0)
      const result = calculateInsertText('World', 0, 0, 'Hello');
      expect(result.paddedText).toBe('Hello ');
      expect(result.newValue).toBe('Hello World');
    });

    it('does not add space after when cursor precedes whitespace', () => {
      const result = calculateInsertText(' World', 0, 0, 'Hello');
      expect(result.paddedText).toBe('Hello');
      expect(result.newValue).toBe('Hello World');
    });

    it('does not add space after when cursor is at end', () => {
      const result = calculateInsertText('Hello', 5, 5, 'World');
      expect(result.paddedText).toBe(' World');
      expect(result.newValue).toBe('Hello World');
    });

    it('does not add space after when following char is newline', () => {
      const result = calculateInsertText('\nWorld', 0, 0, 'Hello');
      expect(result.paddedText).toBe('Hello');
    });
  });

  // ==========================================================================
  // Smart Spacing — Both Sides
  // ==========================================================================
  describe('Smart Spacing - Both Sides', () => {
    it('adds space on both sides when surrounded by non-whitespace', () => {
      // "HelloWorld" with cursor between Hello and World (position 5)
      const result = calculateInsertText('HelloWorld', 5, 5, 'Beautiful');
      expect(result.paddedText).toBe(' Beautiful ');
      expect(result.newValue).toBe('Hello Beautiful World');
    });

    it('handles insertion in middle of word', () => {
      // "abcd" with cursor between b and c (position 2)
      const result = calculateInsertText('abcd', 2, 2, 'X');
      expect(result.paddedText).toBe(' X ');
      expect(result.newValue).toBe('ab X cd');
    });
  });

  // ==========================================================================
  // Selection Replacement
  // ==========================================================================
  describe('Selection Replacement', () => {
    it('replaces selected text', () => {
      // "Hello World" with "World" selected (positions 6-11)
      const result = calculateInsertText('Hello World', 6, 11, 'Universe');
      expect(result.newValue).toBe('Hello Universe');
    });

    it('replaces selection and adds space before if needed', () => {
      // "HelloWorld" with "World" selected (positions 5-10)
      const result = calculateInsertText('HelloWorld', 5, 10, 'Universe');
      expect(result.newValue).toBe('Hello Universe');
    });

    it('replaces selection and adds space after if needed', () => {
      // "HelloWorld" with "Hello" selected (positions 0-5)
      const result = calculateInsertText('HelloWorld', 0, 5, 'Hi');
      expect(result.newValue).toBe('Hi World');
    });

    it('replaces entire content when all selected', () => {
      const result = calculateInsertText('Old Text', 0, 8, 'New Text');
      expect(result.newValue).toBe('New Text');
      expect(result.newCursorPos).toBe(8);
    });

    it('handles selection at word boundary correctly', () => {
      // "Hello World" with " World" selected (positions 5-11)
      const result = calculateInsertText('Hello World', 5, 11, ' Universe');
      expect(result.newValue).toBe('Hello Universe');
    });
  });

  // ==========================================================================
  // Edge Cases
  // ==========================================================================
  describe('Edge Cases', () => {
    it('handles empty text to insert', () => {
      const result = calculateInsertText('Hello World', 5, 5, '');
      expect(result.newValue).toBe('Hello World');
      expect(result.newCursorPos).toBe(5);
    });

    it('handles text with special characters', () => {
      const result = calculateInsertText('Hello', 5, 5, '/skill');
      expect(result.newValue).toBe('Hello /skill');
    });

    it('handles text starting with slash (skill format)', () => {
      const result = calculateInsertText('Check this', 10, 10, '/analyze');
      expect(result.newValue).toBe('Check this /analyze');
    });

    it('handles file paths', () => {
      const result = calculateInsertText('Read', 4, 4, './src/file.ts');
      expect(result.newValue).toBe('Read ./src/file.ts');
    });

    it('handles multiline text insertion', () => {
      const result = calculateInsertText('AB', 1, 1, 'X\nY');
      expect(result.paddedText).toBe(' X\nY ');
      expect(result.newValue).toBe('A X\nY B');
    });

    it('cursor position accounts for added spaces', () => {
      // "AB" with cursor at position 1, inserting "X"
      // Result: "A X B" — cursor should be after "X " (position 4)
      const result = calculateInsertText('AB', 1, 1, 'X');
      expect(result.newCursorPos).toBe(4); // "A X " = 4 chars
    });
  });

  // ==========================================================================
  // Skill Insertion Scenarios
  // ==========================================================================
  describe('Skill Insertion Scenarios', () => {
    it('inserts skill at beginning of empty input', () => {
      const result = calculateInsertText('', 0, 0, '/commit');
      expect(result.newValue).toBe('/commit');
    });

    it('inserts skill at end of existing text', () => {
      const result = calculateInsertText('Please', 6, 6, '/commit');
      expect(result.newValue).toBe('Please /commit');
    });

    it('inserts skill in middle of text', () => {
      const result = calculateInsertText('Please this file', 7, 7, '/analyze');
      expect(result.newValue).toBe('Please /analyze this file');
    });

    it('replaces selected text with skill', () => {
      const result = calculateInsertText('Run the old command', 8, 11, '/new');
      expect(result.newValue).toBe('Run the /new command');
    });
  });

  // ==========================================================================
  // File Path Insertion Scenarios
  // ==========================================================================
  describe('File Path Insertion Scenarios', () => {
    it('inserts file path at cursor', () => {
      const result = calculateInsertText('Check the file', 14, 14, './src/main.ts');
      expect(result.newValue).toBe('Check the file ./src/main.ts');
    });

    it('inserts file path in middle', () => {
      const result = calculateInsertText('Read and analyze', 4, 4, './data.json');
      expect(result.newValue).toBe('Read ./data.json and analyze');
    });

    it('replaces selection with file path', () => {
      const result = calculateInsertText('Check the old/path.txt file', 10, 22, './new/file.ts');
      expect(result.newValue).toBe('Check the ./new/file.ts file');
    });
  });
});
