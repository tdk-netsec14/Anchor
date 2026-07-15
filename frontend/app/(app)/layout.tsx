import { AppShell } from "@/components/layout/AppShell";

/**
 * Everything inside this group is behind the session gate. The group name is
 * kept out of the URL, so routes stay at /assistant, /knowledge and so on.
 */
export default function WorkspaceLayout({ children }: { children: React.ReactNode }) {
  return <AppShell>{children}</AppShell>;
}
