import * as React from "react"
import { cva, type VariantProps } from "class-variance-authority"
import { cn } from "@/lib/utils"

const badgeVariants = cva(
  "inline-flex items-center rounded-md border px-1.5 py-0.5 text-[11px] font-medium whitespace-nowrap",
  {
    variants: {
      variant: {
        default: "border-border bg-muted text-muted-foreground",
        speech: "border-transparent bg-[color-mix(in_oklch,var(--color-speech)_12%,white)] text-[var(--color-speech)]",
        visual: "border-transparent bg-[color-mix(in_oklch,var(--color-visual)_12%,white)] text-[var(--color-visual)]",
        ocr: "border-transparent bg-[color-mix(in_oklch,var(--color-ocr)_14%,white)] text-[var(--color-ocr)]",
        disputed: "border-transparent bg-[color-mix(in_oklch,var(--color-disputed)_12%,white)] text-[var(--color-disputed)]",
        supported: "border-transparent bg-[color-mix(in_oklch,var(--color-supported)_12%,white)] text-[var(--color-supported)]",
      },
    },
    defaultVariants: { variant: "default" },
  }
)
function Badge({ className, variant, ...props }: React.ComponentProps<"span"> & VariantProps<typeof badgeVariants>) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />
}
export { Badge }
