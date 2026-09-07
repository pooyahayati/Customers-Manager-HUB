import Link from "next/link";

export default function Home() {
  return (
    <main className="bootstrap">
      <h1>Customers Manager HUB</h1>
      <p>System bootstrap running</p>
      <Link href="/analytics">Open analytics foundation</Link>
    </main>
  );
}
