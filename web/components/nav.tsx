import Link from "next/link";

const LINKS = [
  { href: "/", label: "Arena" },
  { href: "/methods", label: "Methods" },
  { href: "/verify", label: "Verify" },
];

export function Nav() {
  return (
    <header className="border-b border-neutral-200">
      <div className="mx-auto flex max-w-5xl items-center justify-between gap-4 px-4 py-4 sm:px-6">
        <Link href="/" className="text-sm font-semibold tracking-tight">
          Random Walk Arena
        </Link>
        <nav className="flex gap-4 text-sm text-neutral-600">
          {LINKS.map((link) => (
            <Link key={link.href} href={link.href} className="hover:text-neutral-950">
              {link.label}
            </Link>
          ))}
        </nav>
      </div>
    </header>
  );
}
