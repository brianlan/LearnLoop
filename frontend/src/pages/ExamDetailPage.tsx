import { useParams } from "react-router-dom";

export function ExamDetailPage() {
  const { id } = useParams<{ id: string }>();

  return (
    <main>
      <h1>Exam {id}</h1>
      <p>Exam detail page placeholder</p>
    </main>
  );
}
