import type { Metadata } from "next";
import { Rajdhani, Share_Tech_Mono } from "next/font/google";
import "./globals.css";

const mono = Share_Tech_Mono({ variable: "--font-mono", weight: "400", subsets: ["latin"] });
const display = Rajdhani({ variable: "--font-display", weight: ["500", "600", "700"], subsets: ["latin"] });

export const metadata: Metadata = {
  title: "MandiPulse",
  description:
    "Live wholesale commodity prices across India's mandis, from Agmarknet open data, with ML price forecasts and anomaly detection.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className={`${mono.variable} ${display.variable} lg:h-full`}>
      <body className="min-h-screen lg:h-full lg:overflow-hidden">{children}</body>
    </html>
  );
}
