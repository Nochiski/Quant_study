import type { ReactNode } from "react";

type EmptyStateProps = {
  title: string;
  description?: ReactNode;
  action?: ReactNode;
  className?: string;
};

export const EmptyState = ({
  title,
  description,
  action,
  className = "",
}: EmptyStateProps) => (
  <div role="status" className={`ui-empty-state ${className}`.trim()}>
    <p className="ui-empty-state__title">{title}</p>
    {description ? (
      <p className="ui-empty-state__description">{description}</p>
    ) : null}
    {action}
  </div>
);
