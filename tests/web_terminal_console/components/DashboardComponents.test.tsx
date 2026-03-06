/**
 * Tests for shared dashboard components:
 * TabbedDetail, FormControls (FormField, SpendingBar, CheckboxGroup,
 * TagInput, JsonEditor, ImpactConfirmDialog, ReadonlyField)
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { TabbedDetail } from '../../../src/web_terminal_client/src/components/dashboard/TabbedDetail';
import {
  FormField,
  ReadonlyField,
  SpendingBar,
  CheckboxGroup,
  TagInput,
  JsonEditor,
  ImpactConfirmDialog,
} from '../../../src/web_terminal_client/src/components/dashboard/FormControls';

// ---------------------------------------------------------------------------
// TabbedDetail
// ---------------------------------------------------------------------------

describe('TabbedDetail', () => {
  const tabs = [
    { id: 'overview', label: 'Overview' },
    { id: 'config', label: 'Config', badge: 3 },
  ];

  it('renders tab buttons', () => {
    render(
      <TabbedDetail tabs={tabs} activeTab="overview" onTabChange={() => {}}>
        <div>Content</div>
      </TabbedDetail>,
    );
    expect(screen.getByText('Overview')).toBeInTheDocument();
    expect(screen.getByText('Config')).toBeInTheDocument();
  });

  it('shows badge on tab', () => {
    render(
      <TabbedDetail tabs={tabs} activeTab="overview" onTabChange={() => {}}>
        <div>Content</div>
      </TabbedDetail>,
    );
    expect(screen.getByText('3')).toBeInTheDocument();
  });

  it('highlights active tab', () => {
    render(
      <TabbedDetail tabs={tabs} activeTab="overview" onTabChange={() => {}}>
        <div>Content</div>
      </TabbedDetail>,
    );
    const overviewBtn = screen.getByText('Overview').closest('button');
    expect(overviewBtn?.className).toContain('dash-tab-active');
  });

  it('calls onTabChange when tab is clicked', () => {
    const onChange = vi.fn();
    render(
      <TabbedDetail tabs={tabs} activeTab="overview" onTabChange={onChange}>
        <div>Content</div>
      </TabbedDetail>,
    );
    fireEvent.click(screen.getByText('Config'));
    expect(onChange).toHaveBeenCalledWith('config');
  });

  it('renders children', () => {
    render(
      <TabbedDetail tabs={tabs} activeTab="overview" onTabChange={() => {}}>
        <div>Tab Content</div>
      </TabbedDetail>,
    );
    expect(screen.getByText('Tab Content')).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// FormField
// ---------------------------------------------------------------------------

describe('FormField', () => {
  it('renders label and children', () => {
    render(
      <FormField label="Username">
        <input data-testid="input" />
      </FormField>,
    );
    expect(screen.getByText('Username')).toBeInTheDocument();
    expect(screen.getByTestId('input')).toBeInTheDocument();
  });

  it('shows required indicator', () => {
    render(
      <FormField label="Email" required>
        <input />
      </FormField>,
    );
    expect(screen.getByText('*')).toBeInTheDocument();
  });

  it('shows error message', () => {
    render(
      <FormField label="Password" error="Too short">
        <input />
      </FormField>,
    );
    expect(screen.getByText('Too short')).toBeInTheDocument();
  });

  it('shows hint when no error', () => {
    render(
      <FormField label="Name" hint="3-32 chars">
        <input />
      </FormField>,
    );
    expect(screen.getByText('3-32 chars')).toBeInTheDocument();
  });

  it('hides hint when error is present', () => {
    render(
      <FormField label="Name" hint="3-32 chars" error="Invalid">
        <input />
      </FormField>,
    );
    expect(screen.queryByText('3-32 chars')).not.toBeInTheDocument();
    expect(screen.getByText('Invalid')).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// ReadonlyField
// ---------------------------------------------------------------------------

describe('ReadonlyField', () => {
  it('renders label and value', () => {
    render(<ReadonlyField label="Role" value="admin" />);
    expect(screen.getByText('Role')).toBeInTheDocument();
    expect(screen.getByText('admin')).toBeInTheDocument();
  });

  it('shows dash for null value', () => {
    render(<ReadonlyField label="Company" value={null} />);
    const dashEm = screen.getByText((_: string, el: Element | null) => el?.textContent === '\u2014');
    expect(dashEm).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// SpendingBar
// ---------------------------------------------------------------------------

describe('SpendingBar', () => {
  it('shows no limit text when limit is null', () => {
    render(<SpendingBar current={42.5} limit={null} />);
    expect(screen.getByText('$42.50 (no limit)')).toBeInTheDocument();
  });

  it('shows percentage when limit is set', () => {
    render(<SpendingBar current={50} limit={100} />);
    expect(screen.getByText('$50.00 / $100.00 (50%)')).toBeInTheDocument();
  });

  it('caps at 100%', () => {
    render(<SpendingBar current={150} limit={100} />);
    expect(screen.getByText('$150.00 / $100.00 (100%)')).toBeInTheDocument();
  });

  it('renders label when provided', () => {
    render(<SpendingBar current={10} limit={100} label="Monthly" />);
    expect(screen.getByText('Monthly')).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// CheckboxGroup
// ---------------------------------------------------------------------------

describe('CheckboxGroup', () => {
  const options = [
    { value: 'a', label: 'Option A' },
    { value: 'b', label: 'Option B' },
    { value: 'c', label: 'Option C' },
  ];

  it('renders all options', () => {
    render(<CheckboxGroup options={options} selected={[]} onChange={() => {}} />);
    expect(screen.getByText('Option A')).toBeInTheDocument();
    expect(screen.getByText('Option B')).toBeInTheDocument();
    expect(screen.getByText('Option C')).toBeInTheDocument();
  });

  it('checks selected options', () => {
    render(<CheckboxGroup options={options} selected={['a', 'c']} onChange={() => {}} />);
    const checkboxes = screen.getAllByRole('checkbox');
    expect(checkboxes[0]).toBeChecked();
    expect(checkboxes[1]).not.toBeChecked();
    expect(checkboxes[2]).toBeChecked();
  });

  it('calls onChange with toggled selection', () => {
    const onChange = vi.fn();
    render(<CheckboxGroup options={options} selected={['a']} onChange={onChange} />);
    fireEvent.click(screen.getAllByRole('checkbox')[1]); // click Option B
    expect(onChange).toHaveBeenCalledWith(['a', 'b']);
  });

  it('removes from selection on uncheck', () => {
    const onChange = vi.fn();
    render(<CheckboxGroup options={options} selected={['a', 'b']} onChange={onChange} />);
    fireEvent.click(screen.getAllByRole('checkbox')[0]); // uncheck Option A
    expect(onChange).toHaveBeenCalledWith(['b']);
  });
});

// ---------------------------------------------------------------------------
// TagInput
// ---------------------------------------------------------------------------

describe('TagInput', () => {
  it('renders existing tags', () => {
    render(<TagInput tags={['192.168.1.0/24', '10.0.0.1']} onChange={() => {}} />);
    expect(screen.getByText('192.168.1.0/24')).toBeInTheDocument();
    expect(screen.getByText('10.0.0.1')).toBeInTheDocument();
  });

  it('adds a tag on Enter', () => {
    const onChange = vi.fn();
    render(<TagInput tags={['existing']} onChange={onChange} />);
    const input = screen.getByPlaceholderText('Add...');
    fireEvent.change(input, { target: { value: 'new-tag' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(onChange).toHaveBeenCalledWith(['existing', 'new-tag']);
  });

  it('adds a tag on Add button click', () => {
    const onChange = vi.fn();
    render(<TagInput tags={[]} onChange={onChange} />);
    const input = screen.getByPlaceholderText('Add...');
    fireEvent.change(input, { target: { value: 'new-tag' } });
    fireEvent.click(screen.getByText('Add'));
    expect(onChange).toHaveBeenCalledWith(['new-tag']);
  });

  it('does not add duplicate tags', () => {
    const onChange = vi.fn();
    render(<TagInput tags={['existing']} onChange={onChange} />);
    const input = screen.getByPlaceholderText('Add...');
    fireEvent.change(input, { target: { value: 'existing' } });
    fireEvent.click(screen.getByText('Add'));
    expect(onChange).not.toHaveBeenCalled();
  });

  it('shows validation error', () => {
    const validate = (val: string) => val.includes('.') ? null : 'Must contain a dot';
    render(<TagInput tags={[]} onChange={() => {}} validate={validate} />);
    const input = screen.getByPlaceholderText('Add...');
    fireEvent.change(input, { target: { value: 'nodot' } });
    fireEvent.click(screen.getByText('Add'));
    expect(screen.getByText('Must contain a dot')).toBeInTheDocument();
  });

  it('removes a tag', () => {
    const onChange = vi.fn();
    render(<TagInput tags={['a', 'b']} onChange={onChange} />);
    const removeButtons = screen.getAllByText('\u00d7');
    fireEvent.click(removeButtons[0]);
    expect(onChange).toHaveBeenCalledWith(['b']);
  });
});

// ---------------------------------------------------------------------------
// JsonEditor
// ---------------------------------------------------------------------------

describe('JsonEditor', () => {
  it('renders initial JSON', () => {
    render(<JsonEditor value={{ key: 'value' }} onChange={() => {}} />);
    const textarea = screen.getByRole('textbox');
    expect(textarea).toHaveValue(JSON.stringify({ key: 'value' }, null, 2));
  });

  it('calls onChange with parsed JSON', () => {
    const onChange = vi.fn();
    render(<JsonEditor value={{}} onChange={onChange} />);
    const textarea = screen.getByRole('textbox');
    fireEvent.change(textarea, { target: { value: '{"a": 1}' } });
    expect(onChange).toHaveBeenCalledWith({ a: 1 });
  });

  it('shows error for invalid JSON', () => {
    render(<JsonEditor value={{}} onChange={() => {}} />);
    const textarea = screen.getByRole('textbox');
    fireEvent.change(textarea, { target: { value: '{invalid' } });
    expect(screen.getByText('Invalid JSON')).toBeInTheDocument();
  });

  it('shows error for non-object JSON', () => {
    render(<JsonEditor value={{}} onChange={() => {}} />);
    const textarea = screen.getByRole('textbox');
    fireEvent.change(textarea, { target: { value: '"just a string"' } });
    expect(screen.getByText('Must be a JSON object')).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// ImpactConfirmDialog
// ---------------------------------------------------------------------------

describe('ImpactConfirmDialog', () => {
  it('does not render when closed', () => {
    render(
      <ImpactConfirmDialog
        open={false}
        title="Delete"
        entityName="test"
        impact={[]}
        onConfirm={() => {}}
        onCancel={() => {}}
      />,
    );
    expect(screen.queryByText('Delete')).not.toBeInTheDocument();
  });

  it('renders impact list when open', () => {
    render(
      <ImpactConfirmDialog
        open={true}
        title="Delete Reseller"
        entityName="acme"
        impact={[
          { label: 'users', count: 5 },
          { label: 'sessions', count: 42 },
        ]}
        onConfirm={() => {}}
        onCancel={() => {}}
      />,
    );
    expect(screen.getByText('Delete Reseller')).toBeInTheDocument();
    expect(screen.getByText('5')).toBeInTheDocument();
    expect(screen.getByText('users')).toBeInTheDocument();
    expect(screen.getByText('42')).toBeInTheDocument();
  });

  it('disables confirm until name is typed', () => {
    render(
      <ImpactConfirmDialog
        open={true}
        title="Delete Item"
        entityName="acme"
        impact={[]}
        onConfirm={() => {}}
        onCancel={() => {}}
      />,
    );
    const deleteBtn = screen.getByRole('button', { name: 'Delete' });
    expect(deleteBtn).toBeDisabled();

    const input = screen.getByPlaceholderText('acme');
    fireEvent.change(input, { target: { value: 'acme' } });
    expect(deleteBtn).not.toBeDisabled();
  });

  it('calls onConfirm when name matches and button is clicked', () => {
    const onConfirm = vi.fn();
    render(
      <ImpactConfirmDialog
        open={true}
        title="Delete Item"
        entityName="test-name"
        impact={[]}
        onConfirm={onConfirm}
        onCancel={() => {}}
      />,
    );
    const input = screen.getByPlaceholderText('test-name');
    fireEvent.change(input, { target: { value: 'test-name' } });
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
    expect(onConfirm).toHaveBeenCalled();
  });

  it('calls onCancel when Cancel is clicked', () => {
    const onCancel = vi.fn();
    render(
      <ImpactConfirmDialog
        open={true}
        title="Delete"
        entityName="test"
        impact={[]}
        onConfirm={() => {}}
        onCancel={onCancel}
      />,
    );
    fireEvent.click(screen.getByText('Cancel'));
    expect(onCancel).toHaveBeenCalled();
  });
});
