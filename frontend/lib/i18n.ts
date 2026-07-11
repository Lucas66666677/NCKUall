import enUS from "@/messages/en-US.json";
import zhTW from "@/messages/zh-TW.json";

export const LOCALES = ["zh-TW", "en-US"] as const;

export type Locale = (typeof LOCALES)[number];
export type TranslationKey = keyof typeof zhTW;

export const DEFAULT_LOCALE: Locale = "zh-TW";

export const translations = {
  "zh-TW": zhTW,
  "en-US": enUS,
} satisfies Record<Locale, Record<TranslationKey, string>>;

export function isLocale(value: string | null | undefined): value is Locale {
  return LOCALES.includes(value as Locale);
}

export function translate(
  locale: Locale,
  key: TranslationKey,
  values?: Record<string, string | number>,
) {
  const template = translations[locale][key] ?? translations[DEFAULT_LOCALE][key];
  if (!values) {
    return template;
  }

  return Object.entries(values).reduce(
    (message, [name, value]) =>
      message.replaceAll(`{${name}}`, String(value)),
    template,
  );
}
