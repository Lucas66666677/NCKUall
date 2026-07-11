import { LifeClient } from "@/app/life/LifeClient";
import { getLifeReviews } from "@/lib/server-data";

export const revalidate = 86400;

export default async function LifePage() {
  const initialReviews = await getLifeReviews();
  return <LifeClient initialReviews={initialReviews} />;
}
