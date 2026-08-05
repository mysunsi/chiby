import { cva, type VariantProps } from "class-variance-authority";
import * as React from "react";
import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "ops-inline-flex ops-items-center ops-justify-center ops-rounded-lg ops-font-medium ops-transition-colors focus:ops-outline-none focus:ops-ring-2 focus:ops-ring-offset-2 disabled:ops-pointer-events-none disabled:ops-opacity-50",
  {
    variants: {
      variant: {
        primary:
          "ops-bg-green-600 ops-text-white hover:ops-bg-green-700 focus:ops-ring-green-500",
        secondary:
          "ops-border ops-border-gray-300 ops-bg-white ops-text-gray-900 hover:ops-bg-gray-50 focus:ops-ring-gray-400",
        danger:
          "ops-bg-red-600 ops-text-white hover:ops-bg-red-700 focus:ops-ring-red-500",
        ghost:
          "ops-bg-transparent ops-text-gray-600 hover:ops-bg-gray-100 focus:ops-ring-gray-400",
        warning:
          "ops-bg-yellow-500 ops-text-white hover:ops-bg-yellow-600 focus:ops-ring-yellow-500",
      },
      size: {
        lg: "ops-h-11 ops-px-6 ops-text-sm",
        md: "ops-h-9 ops-px-4 ops-text-xs",
      },
    },
    defaultVariants: {
      variant: "primary",
      size: "lg",
    },
  },
);

function SpinnerIcon({ className }: { className?: string }) {
  return (
    <svg
      className={cn("ops-mr-2 ops-h-4 ops-w-4 ops-shrink-0 ops-animate-spin", className)}
      xmlns="http://www.w3.org/2000/svg"
      fill="none"
      viewBox="0 0 24 24"
      aria-hidden
    >
      <circle
        className="ops-opacity-25"
        cx="12"
        cy="12"
        r="10"
        stroke="currentColor"
        strokeWidth="4"
      />
      <path
        className="ops-opacity-75"
        fill="currentColor"
        d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
      />
    </svg>
  );
}

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  isLoading?: boolean;
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  (
    { className, variant, size, isLoading, children, disabled, ...props },
    ref,
  ) => {
    return (
      <button
        ref={ref}
        className={cn(buttonVariants({ variant, size }), className)}
        disabled={disabled || isLoading}
        {...props}
      >
        {isLoading ? (
          <>
            <SpinnerIcon />
            {children}
          </>
        ) : (
          children
        )}
      </button>
    );
  },
);
Button.displayName = "Button";
