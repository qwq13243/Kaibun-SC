import type { Metadata } from "next";
import { OutfitReview } from "@/components/kaibun/OutfitReview";

export const metadata: Metadata = { title: "默认衣装选择 · Kaibun" };

export default function Page() {
  return <OutfitReview />;
}
