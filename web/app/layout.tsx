import type { Metadata } from "next";
import { Inter } from "next/font/google";

import { Nav } from "@/components/nav";
import { SITE_HOST, SITE_NAME } from "@/lib/config";

import "./globals.css";

const sans = Inter({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-sans",
});

export const metadata: Metadata = {
  metadataBase: new URL(`https://${SITE_HOST}`),
  title: {
    default: SITE_NAME,
    template: `%s · ${SITE_NAME}`,
  },
  description: "Can AI forecast markets better than a random walk?",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={sans.variable}>
      <body className="min-h-screen bg-white font-sans text-neutral-950 antialiased">
        <Nav />
        {children}
      </body>
    </html>
  );
}
