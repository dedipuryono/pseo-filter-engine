import rawCatalog from '../data/catalog.json';

export interface FilterProduct {
  asin: string;
  title: string;
  is_oem: boolean;
  rating: number | null;
  review_count: number;
  pros: string[];
  cons: string[];
}

export interface DeviceEntry {
  id: number;
  brand_name: string;
  brand_slug: string;
  model_name: string;
  slug: string;
  category: 'air_purifier' | 'vacuum';
  official_filter_code: string | null;
  hepa_grade: string | null;
  filter_shape: string | null;
  dimensions: {
    length_in: number;
    width_in: number;
    height_in: number;
  } | null;
  compatibility_warning: string;
  faqs: { q: string; a: string }[];
  direct_answer_capsule: string;
  key_takeaways: string[];
  published_at: string;
  updated_at: string;
  products: FilterProduct[];
}

export function getAllDevices(): DeviceEntry[] {
  return (rawCatalog as DeviceEntry[]) || [];
}

export function getBrandsSummary() {
  const devices = getAllDevices();
  const map = new Map<string, { name: string; slug: string; category: string; count: number }>();

  for (const d of devices) {
    const existing = map.get(d.brand_slug);
    if (existing) {
      existing.count += 1;
    } else {
      map.set(d.brand_slug, {
        name: d.brand_name,
        slug: d.brand_slug,
        category: d.category,
        count: 1,
      });
    }
  }
  return Array.from(map.values()).sort((a, b) => b.count - a.count);
}