import React from 'react';

type Variant = 'primary' | 'ghost' | 'danger' | 'success' | 'warn';
type Size = 'sm' | 'md' | 'lg';

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  loading?: boolean;
  icon?: React.ReactNode;
}

export function Button({
  children, variant = 'ghost', size = 'md', loading, icon,
  className, disabled, ...rest
}: ButtonProps) {
  return (
    <button
      {...rest}
      disabled={disabled || loading}
      className={`btn ${variant}${size !== 'md' ? ` ${size}` : ''}${className ? ` ${className}` : ''}`}
    >
      {loading ? <span style={{ opacity: 0.7 }}>…</span> : icon}
      {children}
    </button>
  );
}
