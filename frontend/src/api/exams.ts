import { api } from "./client";
import type {
  CreateExamRequest,
  CreateExamResponse,
  ExamHistoryResponse,
  ExamResponse,
  SaveAnswerRequest,
  SaveAnswerResponse,
  SelfReportRequest,
  SelfReportResponse,
} from "@/types/exam";

export async function getExamHistory(
  page: number,
  pageSize: number,
  includeDiscarded: boolean,
): Promise<ExamHistoryResponse> {
  return api.get<ExamHistoryResponse>(
    `/exams?page=${page}&pageSize=${pageSize}&includeDiscarded=${includeDiscarded}`,
  );
}

export async function createExam(
  request: CreateExamRequest,
): Promise<CreateExamResponse> {
  return api.post<CreateExamResponse>("/exams", request);
}

export async function getActiveExam(): Promise<ExamResponse> {
  return api.get<ExamResponse>("/exams/active");
}

export async function getExam(examId: string): Promise<ExamResponse> {
  return api.get<ExamResponse>(`/exams/${examId}`);
}

export async function saveExamAnswer(
  examId: string,
  itemId: string,
  request: SaveAnswerRequest,
): Promise<SaveAnswerResponse> {
  return api.patch<SaveAnswerResponse>(
    `/exams/${examId}/items/${itemId}/answer`,
    request,
  );
}

export async function submitExam(examId: string): Promise<ExamResponse> {
  return api.post<ExamResponse>(`/exams/${examId}/submit`, {});
}

export async function discardExam(examId: string): Promise<ExamResponse> {
  return api.post<ExamResponse>(`/exams/${examId}/discard`, {});
}

export async function selfReportExamItem(
  examId: string,
  itemId: string,
  request: SelfReportRequest,
): Promise<SelfReportResponse> {
  return api.post<SelfReportResponse>(
    `/exams/${examId}/items/${itemId}/self-report`,
    request,
  );
}