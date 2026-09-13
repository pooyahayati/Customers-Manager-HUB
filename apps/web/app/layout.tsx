import type { Metadata } from "next";
import localFont from "next/font/local";
import type { ReactNode } from "react";

import "./globals.css";

const vazirmatn = localFont({
  src: "./fonts/Vazirmatn[wght].woff2",
  display: "swap",
  variable: "--font-vazirmatn",
  weight: "100 900",
});

export const metadata: Metadata = {
  title: "Customers Manager HUB",
  description: "Customers Manager HUB administration console",
};

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <html
      className={vazirmatn.variable}
      lang="en"
      dir="ltr"
      data-theme="light"
      suppressHydrationWarning
    >
      <head>
        <script
          dangerouslySetInnerHTML={{
            __html: `try{var t=localStorage.getItem("cmh-theme");var l=localStorage.getItem("cmh-locale");if(t!=="light"&&t!=="dark"){t=matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light"}if(l!=="fa"&&l!=="en"){l="en"}document.documentElement.dataset.theme=t;document.documentElement.style.colorScheme=t;document.documentElement.lang=l;document.documentElement.dir=l==="fa"?"rtl":"ltr"}catch(e){}`,
          }}
        />
      </head>
      <body>{children}</body>
    </html>
  );
}
