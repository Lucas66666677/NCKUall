export type DepartmentApiResponse = {
  id: string;
  code: string;
  name_zh: string;
  name_en: string | null;
  college: string | null;
  is_active: boolean;
};

export type CourseDifficulty = "easy" | "medium" | "hard" | "unknown";

export type GradeDistribution = {
  id: string;
  academic_year: number;
  semester: number;
  enrollment_count: number | null;
  avg_score: string | number | null;
  median_score: string | number | null;
  pass_rate: string | number | null;
  grade_buckets: Record<string, number | string | null>;
  source_url: string | null;
};

export type Course = {
  id: string;
  department_id: string;
  course_code: string;
  title_zh: string;
  title_en: string | null;
  instructor_name: string | null;
  academic_year: number | null;
  semester: number | null;
  credits: string | number | null;
  required_for_major: boolean;
  description: string | null;
  difficulty: CourseDifficulty;
  grade_distributions: GradeDistribution[];
};

export type RecommendationResourceType = "course" | "career";

export type RecommendationItem = {
  resource_type: RecommendationResourceType;
  resource_id: string;
  title: string;
  subtitle: string | null;
  department_id: string | null;
  department_name: string | null;
  href: string;
  reason: string;
  similarity_score: number;
  adjusted_score: number;
  tags: string[];
};

export type RecommendationResponse = {
  items: RecommendationItem[];
  based_on_count: number;
  viewed_resource_count: number;
  profile_ready: boolean;
};

export type LifeReviewType =
  | "rental_warning"
  | "rental_recommendation"
  | "food_recommendation"
  | "protein_meal_prep"
  | "other";

export type LifeReview = {
  id: string;
  review_type: LifeReviewType;
  title: string;
  content: string;
  location_name: string | null;
  area: string | null;
  address: string | null;
  rating: number | null;
  price_level: number | null;
  author_alias: string | null;
  tags: string[];
  is_verified: boolean;
  created_at: string;
};
