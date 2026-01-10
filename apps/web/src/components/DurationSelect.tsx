/**
 * DurationSelect - Base UI prototype component
 *
 * This is a prototype demonstrating how Base UI Select could replace
 * the native <select> element for the report duration picker.
 *
 * Base UI provides:
 * - Full keyboard navigation (arrow keys, type-ahead)
 * - ARIA attributes for screen readers
 * - Customizable styling (unstyled by default)
 * - Portal-based positioning (no overflow issues)
 */

import { Select } from '@base-ui-components/react/select';

interface DurationOption {
  value: string;
  label: string;
}

interface DurationSelectProps {
  /** Currently selected duration value */
  value: string;
  /** Callback when selection changes */
  onChange: (value: string | null) => void;
  /** Optional CSS class for the trigger */
  className?: string;
}

const DURATION_OPTIONS: DurationOption[] = [
  { value: '24h', label: 'Last 24 hours' },
  { value: '7d', label: 'Last 7 days' },
  { value: '30d', label: 'Last 30 days' },
];

export function DurationSelect({ value, onChange, className = '' }: DurationSelectProps) {
  return (
    <Select.Root value={value} onValueChange={onChange}>
      <Select.Trigger className={`report-select ${className}`.trim()}>
        <Select.Value />
        <Select.Icon>
          <ChevronDown />
        </Select.Icon>
      </Select.Trigger>
      <Select.Portal>
        <Select.Positioner sideOffset={4}>
          <Select.Popup className="select-popup">
            {DURATION_OPTIONS.map((option) => (
              <Select.Item
                key={option.value}
                value={option.value}
                className="select-item"
              >
                <Select.ItemIndicator className="select-item-indicator">
                  <Check />
                </Select.ItemIndicator>
                <Select.ItemText>{option.label}</Select.ItemText>
              </Select.Item>
            ))}
          </Select.Popup>
        </Select.Positioner>
      </Select.Portal>
    </Select.Root>
  );
}

// Simple SVG icons (could be extracted to shared package)
function ChevronDown() {
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" fill="currentColor">
      <path d="M2.5 4.5L6 8L9.5 4.5" stroke="currentColor" strokeWidth="1.5" fill="none" />
    </svg>
  );
}

function Check() {
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" fill="currentColor">
      <path d="M2.5 6L5 8.5L9.5 3.5" stroke="currentColor" strokeWidth="1.5" fill="none" />
    </svg>
  );
}

export default DurationSelect;
