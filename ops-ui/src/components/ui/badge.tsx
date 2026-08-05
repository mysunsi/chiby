import { cva, type VariantProps } from "class-variance-authority";
import * as React from "react";
import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "ops-inline-flex ops-items-center ops-rounded-md ops-border ops-px-1.5 ops-py-0.5 ops-text-[10px] ops-font-semibold ops-leading-none ops-transition-colors",
  {
    variants: {
      variant: {
        default:
          "ops-border-transparent ops-bg-gray-100 ops-text-gray-800 hover:ops-bg-gray-200",
        secondary:
          "ops-border-transparent ops-bg-gray-700 ops-text-gray-50 hover:ops-bg-gray-600",
        outline: "ops-border-gray-300 ops-bg-transparent ops-text-gray-700",
        retry:
          "ops-border-amber-400 ops-bg-amber-50 ops-text-amber-900 hover:ops-bg-amber-100",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  },
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, ...props }: BadgeProps) {
  return <div className={cn(badgeVariants({ variant }), className)} {...props} />;
}
