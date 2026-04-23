import React from 'react';

interface CardHeadProps {
  title: string;
  subtitle?: string;
  right?: React.ReactNode;
}

export function CardHead({ title, subtitle, right }: CardHeadProps) {
  return (
    <div className="card-head">
      <div className="card-head-left">
        <span className="card-title">{title}</span>
        {subtitle && <span className="card-subtitle">{subtitle}</span>}
      </div>
      {right && <div>{right}</div>}
    </div>
  );
}

interface CardBodyProps {
  children: React.ReactNode;
  tight?: boolean;
  flush?: boolean;
}

export function CardBody({ children, tight, flush }: CardBodyProps) {
  return (
    <div className={`card-body${tight ? ' tight' : ''}${flush ? ' flush' : ''}`}>
      {children}
    </div>
  );
}

interface CardProps {
  children: React.ReactNode;
  style?: React.CSSProperties;
  className?: string;
}

export function Card({ children, style, className }: CardProps) {
  return (
    <div className={`card${className ? ` ${className}` : ''}`} style={style}>
      {children}
    </div>
  );
}
