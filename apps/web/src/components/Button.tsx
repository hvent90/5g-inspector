/**
 * Button - Base UI Button component styled for NetPulse Dashboard
 *
 * Variants:
 * - primary: Magenta accent button for main actions
 * - secondary: Transparent border button for secondary actions
 */

import { Button as BaseButton } from '@base-ui-components/react/button';
import type { ComponentProps, ReactNode } from 'react';

type ButtonVariant = 'primary' | 'secondary';

interface ButtonProps extends Omit<ComponentProps<typeof BaseButton>, 'className'> {
  /** Visual style variant */
  variant?: ButtonVariant;
  /** Button content */
  children: ReactNode;
  /** Optional additional CSS class */
  className?: string;
}

const variantClasses: Record<ButtonVariant, string> = {
  primary: 'btn btn-primary',
  secondary: 'btn btn-secondary',
};

export function Button({
  variant = 'primary',
  children,
  className = '',
  ...props
}: ButtonProps) {
  const baseClass = variantClasses[variant];
  const combinedClass = `${baseClass} ${className}`.trim();

  return (
    <BaseButton className={combinedClass} {...props}>
      {children}
    </BaseButton>
  );
}

export default Button;
