import { useParams } from "react-router-dom";

export function ProblemDetailPage() {
  const { id } = useParams<{ id: string }>();

  return (
    <main>
      <h1>Problem {id}</h1>
      <p>Problem detail page placeholder</p>
    </main>
  );
}
