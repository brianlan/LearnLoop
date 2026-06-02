export type CoachingRole = "student" | "coach";

export interface CoachingMessage {
  role: CoachingRole;
  content: string;
  whiteboard_dsl?: string | null;
  reasoning_content?: string | null;
  created_at: string;
}

export interface CoachingConversation {
  id?: string;
  problem_id: string;
  user_id: string;
  messages: CoachingMessage[];
  created_at: string;
  updated_at: string;
}
