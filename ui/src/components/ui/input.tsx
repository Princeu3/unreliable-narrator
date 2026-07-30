import * as React from "react"
import { cn } from "@/lib/utils"

function Input({ className, ...props }: React.ComponentProps<"input">) {
  return <input className={cn(
    "h-9 w-full rounded-[calc(var(--radius)-2px)] border bg-card px-3 text-sm",
    "placeholder:text-muted-foreground focus-visible:outline-2 focus-visible:outline-offset-0",
    "focus-visible:outline-[var(--color-accent)]", className)} {...props} />
}
export { Input }
