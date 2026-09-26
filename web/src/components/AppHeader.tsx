import type { ReactNode } from "react";
import { Moon, Sun } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { Theme } from "@/lib/use-theme";

interface AppHeaderProps {
  themeControl: { theme: Theme; toggle: () => void };
  /** Wraps onto its own full-width row on phones. */
  picker?: ReactNode;
  children?: ReactNode;
}

export function AppHeader({ themeControl: { theme, toggle }, picker, children }: AppHeaderProps) {
  const next = theme === "light" ? "dark" : "light";
  return (
    <header className="border-b bg-card">
      <div className="mx-auto flex min-h-14 max-w-6xl flex-wrap items-center gap-x-3 gap-y-2 px-4 py-2 sm:px-6">
        <p className="font-semibold">Behavior Review</p>
        <p className="hidden text-sm text-muted-foreground xl:block">AI proposes. Algorithms verify. Humans decide.</p>
        {picker ? <div className="order-last basis-full sm:order-none sm:basis-auto">{picker}</div> : null}
        <div className="ml-auto flex items-center gap-2">
          {children}
          <Tooltip>
            <TooltipTrigger asChild>
              <Button variant="ghost" size="icon" onClick={toggle} aria-label={`Switch to ${next} theme`}>
                {theme === "light" ? <Moon aria-hidden="true" /> : <Sun aria-hidden="true" />}
              </Button>
            </TooltipTrigger>
            <TooltipContent>Switch to {next} theme</TooltipContent>
          </Tooltip>
        </div>
      </div>
    </header>
  );
}
