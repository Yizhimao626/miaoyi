import React from 'react';

interface SkeletonProps {
  variant?: 'text' | 'avatar' | 'bubble' | 'page';
  count?: number;
  className?: string;
}

const SkeletonItem: React.FC<{ variant: string; className?: string }> = ({
  variant,
  className = '',
}) => {
  const baseClass = 'skeleton';
  const variantClass = `skeleton-${variant}`;

  if (variant === 'text') {
    return (
      <div className={`${baseClass} ${variantClass} ${className}`}>
        <div className="skeleton-line" style={{ width: '80%' }} />
        <div className="skeleton-line" style={{ width: '60%' }} />
      </div>
    );
  }

  if (variant === 'avatar') {
    return (
      <div className={`${baseClass} ${variantClass} ${className}`}>
        <div className="skeleton-circle" />
      </div>
    );
  }

  if (variant === 'bubble') {
    return (
      <div className={`${baseClass} ${variantClass} ${className}`}>
        <div className="skeleton-bubble skeleton-bubble-left">
          <div className="skeleton-line" style={{ width: '70%' }} />
          <div className="skeleton-line" style={{ width: '50%' }} />
        </div>
      </div>
    );
  }

  // page variant
  return (
    <div className={`${baseClass} ${variantClass} ${className}`}>
      <div className="skeleton-line" style={{ width: '30%', marginBottom: '16px' }} />
      <div className="skeleton-line" style={{ width: '100%', marginBottom: '12px' }} />
      <div className="skeleton-line" style={{ width: '90%', marginBottom: '12px' }} />
      <div className="skeleton-line" style={{ width: '75%', marginBottom: '24px' }} />
      <div className="skeleton-bubble skeleton-bubble-left">
        <div className="skeleton-line" style={{ width: '60%' }} />
      </div>
      <div className="skeleton-bubble skeleton-bubble-right">
        <div className="skeleton-line" style={{ width: '80%' }} />
        <div className="skeleton-line" style={{ width: '40%' }} />
      </div>
    </div>
  );
};

export const Skeleton: React.FC<SkeletonProps> = ({
  variant = 'text',
  count = 1,
  className = '',
}) => {
  if (count === 1) {
    return <SkeletonItem variant={variant} className={className} />;
  }

  return (
    <div className={`skeleton-group ${className}`}>
      {Array.from({ length: count }).map((_, i) => (
        <SkeletonItem key={i} variant={variant} />
      ))}
    </div>
  );
};

export default Skeleton;
