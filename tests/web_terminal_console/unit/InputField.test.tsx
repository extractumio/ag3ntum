/**
 * Tests for InputField component text insertion behavior
 *
 * Tests the integration of handleInsertText with the textarea element.
 */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import React, { useRef, useState } from 'react';
import { describe, expect, it } from 'vitest';
import { calculateInsertText } from '../../../src/web_terminal_client/src/utils';

/**
 * Test harness that mimics InputField's text insertion behavior.
 * This allows us to test the insertion logic without the full App context.
 */
function TestInputField({
  initialValue = '',
  onValueChange,
}: {
  initialValue?: string;
  onValueChange?: (value: string) => void;
}) {
  const [value, setValue] = useState(initialValue);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const insertTextFnRef = useRef<((text: string) => void) | null>(null);

  // Mimic handleInsertText from App.tsx
  const handleInsertText = React.useCallback((text: string): void => {
    if (!text) return;

    const textarea = textareaRef.current;
    if (!textarea) {
      const needsSpace = value.length > 0 && !/\s$/.test(value);
      const newValue = value + (needsSpace ? ' ' : '') + text;
      setValue(newValue);
      onValueChange?.(newValue);
      return;
    }

    const start = textarea.selectionStart;
    const end = textarea.selectionEnd;

    const { newValue, newCursorPos } = calculateInsertText(value, start, end, text);

    setValue(newValue);
    onValueChange?.(newValue);

    setTimeout(() => {
      textarea.selectionStart = textarea.selectionEnd = newCursorPos;
      textarea.focus();
    }, 0);
  }, [value, onValueChange]);

  // Register the insert function
  React.useEffect(() => {
    insertTextFnRef.current = handleInsertText;
  }, [handleInsertText]);

  return (
    <div>
      <textarea
        ref={textareaRef}
        value={value}
        onChange={(e) => {
          setValue(e.target.value);
          onValueChange?.(e.target.value);
        }}
        data-testid="input-textarea"
      />
      <button
        data-testid="insert-skill"
        onClick={() => insertTextFnRef.current?.('/skill')}
      >
        Insert Skill
      </button>
      <button
        data-testid="insert-file"
        onClick={() => insertTextFnRef.current?.('./src/file.ts')}
      >
        Insert File
      </button>
    </div>
  );
}

describe('InputField Text Insertion', () => {
  // ==========================================================================
  // Basic Insertion
  // ==========================================================================
  describe('Basic Insertion', () => {
    it('inserts text into empty textarea', async () => {
      const user = userEvent.setup();
      render(<TestInputField />);

      const textarea = screen.getByTestId('input-textarea');
      await user.click(screen.getByTestId('insert-skill'));

      expect(textarea).toHaveValue('/skill');
    });

    it('inserts text at end of existing content', async () => {
      const user = userEvent.setup();
      render(<TestInputField initialValue="Hello" />);

      const textarea = screen.getByTestId('input-textarea');
      // Focus and move cursor to end
      await user.click(textarea);
      await user.click(screen.getByTestId('insert-skill'));

      expect(textarea).toHaveValue('Hello /skill');
    });
  });

  // ==========================================================================
  // Cursor Position
  // ==========================================================================
  describe('Cursor Position', () => {
    it('inserts at cursor position in middle of text', async () => {
      const user = userEvent.setup();
      render(<TestInputField initialValue="Hello World" />);

      const textarea = screen.getByTestId('input-textarea') as HTMLTextAreaElement;
      await user.click(textarea);

      // Set cursor position to middle (after "Hello ")
      textarea.setSelectionRange(6, 6);

      await user.click(screen.getByTestId('insert-file'));

      // Should insert with proper spacing
      expect(textarea.value).toBe('Hello ./src/file.ts World');
    });

    it('positions cursor after inserted text', async () => {
      const user = userEvent.setup();
      render(<TestInputField />);

      const textarea = screen.getByTestId('input-textarea') as HTMLTextAreaElement;
      await user.click(screen.getByTestId('insert-skill'));

      // Wait for setTimeout in handleInsertText
      await waitFor(() => {
        expect(textarea.selectionStart).toBe(6); // "/skill" = 6 chars
        expect(textarea.selectionEnd).toBe(6);
      });
    });
  });

  // ==========================================================================
  // Selection Replacement
  // ==========================================================================
  describe('Selection Replacement', () => {
    it('replaces selected text with insertion', async () => {
      const user = userEvent.setup();
      render(<TestInputField initialValue="Hello World" />);

      const textarea = screen.getByTestId('input-textarea') as HTMLTextAreaElement;
      await user.click(textarea);

      // Select "World"
      textarea.setSelectionRange(6, 11);

      await user.click(screen.getByTestId('insert-file'));

      expect(textarea.value).toBe('Hello ./src/file.ts');
    });

    it('replaces all text when fully selected', async () => {
      const user = userEvent.setup();
      render(<TestInputField initialValue="Old content" />);

      const textarea = screen.getByTestId('input-textarea') as HTMLTextAreaElement;
      await user.click(textarea);

      // Select all
      textarea.setSelectionRange(0, 11);

      await user.click(screen.getByTestId('insert-skill'));

      expect(textarea.value).toBe('/skill');
    });
  });

  // ==========================================================================
  // Smart Spacing
  // ==========================================================================
  describe('Smart Spacing', () => {
    it('adds space before when adjacent to non-whitespace', async () => {
      const user = userEvent.setup();
      render(<TestInputField initialValue="Check" />);

      const textarea = screen.getByTestId('input-textarea') as HTMLTextAreaElement;
      await user.click(textarea);
      textarea.setSelectionRange(5, 5); // cursor at end

      await user.click(screen.getByTestId('insert-file'));

      expect(textarea.value).toBe('Check ./src/file.ts');
    });

    it('adds space after when adjacent to non-whitespace', async () => {
      const user = userEvent.setup();
      render(<TestInputField initialValue="World" />);

      const textarea = screen.getByTestId('input-textarea') as HTMLTextAreaElement;
      await user.click(textarea);
      textarea.setSelectionRange(0, 0); // cursor at start

      await user.click(screen.getByTestId('insert-skill'));

      expect(textarea.value).toBe('/skill World');
    });

    it('does not double-space when space already exists', async () => {
      const user = userEvent.setup();
      render(<TestInputField initialValue="Hello " />);

      const textarea = screen.getByTestId('input-textarea') as HTMLTextAreaElement;
      await user.click(textarea);
      textarea.setSelectionRange(6, 6); // cursor after space

      await user.click(screen.getByTestId('insert-skill'));

      expect(textarea.value).toBe('Hello /skill');
    });
  });

  // ==========================================================================
  // Multiple Insertions
  // ==========================================================================
  describe('Multiple Insertions', () => {
    it('handles consecutive insertions correctly', async () => {
      const user = userEvent.setup();
      render(<TestInputField />);

      const textarea = screen.getByTestId('input-textarea') as HTMLTextAreaElement;

      await user.click(screen.getByTestId('insert-skill'));
      expect(textarea.value).toBe('/skill');

      // Wait for cursor positioning
      await waitFor(() => {
        expect(textarea.selectionStart).toBe(6); // "/skill" = 6 chars
      });

      await user.click(screen.getByTestId('insert-file'));
      expect(textarea.value).toBe('/skill ./src/file.ts');
    });
  });

  // ==========================================================================
  // Focus Behavior
  // ==========================================================================
  describe('Focus Behavior', () => {
    it('focuses textarea after insertion', async () => {
      const user = userEvent.setup();
      render(<TestInputField />);

      const textarea = screen.getByTestId('input-textarea');
      const button = screen.getByTestId('insert-skill');

      // Click button (loses focus from textarea)
      await user.click(button);

      // After insertion, textarea should be focused
      await waitFor(() => {
        expect(document.activeElement).toBe(textarea);
      });
    });
  });
});
