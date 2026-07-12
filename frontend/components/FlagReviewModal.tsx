"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import { AlertTriangle, Flag, Loader2, ShieldAlert, X } from "lucide-react";

import { useAppContext } from "@/components/AppContext";
import { getPublicApiBaseUrl } from "@/lib/public-runtime-config";

const API_BASE_URL = getPublicApiBaseUrl();

export type FlagReason =
  | "privacy_attack"
  | "defamation_false"
  | "spam_ad";

const reasonKeys: Record<FlagReason, "flag.reason.privacy" | "flag.reason.defamation" | "flag.reason.spam"> = {
  privacy_attack: "flag.reason.privacy",
  defamation_false: "flag.reason.defamation",
  spam_ad: "flag.reason.spam",
};

const MIN_DESCRIPTION_LENGTH = 50;

type FlagReviewModalProps = {
  reviewId: string;
  reviewTitle: string;
  open: boolean;
  getAccessToken: () => Promise<string | null>;
  onClose: () => void;
  onFlagged: () => void;
};

export function FlagReviewModal({
  reviewId,
  reviewTitle,
  open,
  getAccessToken,
  onClose,
  onFlagged,
}: FlagReviewModalProps) {
  const { t } = useAppContext();
  const [reason, setReason] = useState<FlagReason>("privacy_attack");
  const [description, setDescription] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const remainingCharacters = Math.max(
    0,
    MIN_DESCRIPTION_LENGTH - description.trim().length,
  );
  const canSubmit =
    !isSubmitting && reason && description.trim().length >= MIN_DESCRIPTION_LENGTH;

  const modalTitleId = useMemo(
    () => `flag-review-title-${reviewId}`,
    [reviewId],
  );

  useEffect(() => {
    if (!open) {
      return;
    }

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        onClose();
      }
    }

    document.addEventListener("keydown", handleKeyDown);
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      document.body.style.overflow = "";
    };
  }, [onClose, open]);

  useEffect(() => {
    if (!open) {
      setReason("privacy_attack");
      setDescription("");
      setErrorMessage(null);
      setIsSubmitting(false);
    }
  }, [open]);

  if (!open) {
    return null;
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canSubmit) {
      return;
    }

    setIsSubmitting(true);
    setErrorMessage(null);

    try {
      const accessToken = await getAccessToken();
      if (!accessToken) {
        setErrorMessage(t("flag.error.login"));
        return;
      }

      const adminStatusResponse = await fetch(
        `${API_BASE_URL}/api/admin/reviews/${encodeURIComponent(reviewId)}/status`,
        {
          method: "PUT",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${accessToken}`,
          },
          body: JSON.stringify({
            status: "PENDING",
            report_reason: reason,
            report_description: description.trim(),
          }),
        },
      );

      const response =
        adminStatusResponse.status === 403
          ? await fetch(
              `${API_BASE_URL}/api/life/reviews/${encodeURIComponent(reviewId)}/flag`,
              {
                method: "POST",
                headers: {
                  "Content-Type": "application/json",
                  Authorization: `Bearer ${accessToken}`,
                },
                body: JSON.stringify({
                  report_reason: reason,
                  report_description: description.trim(),
                }),
              },
            )
          : adminStatusResponse;

      if (!response.ok) {
        const payload = (await response.json().catch(() => null)) as {
          detail?: string;
        } | null;
        setErrorMessage(payload?.detail ?? t("flag.error.failed"));
        return;
      }

      onFlagged();
      onClose();
    } catch {
      setErrorMessage(t("flag.error.network"));
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-ink/45 p-0 backdrop-blur-sm sm:items-center sm:p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby={modalTitleId}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) {
          onClose();
        }
      }}
    >
      <div className="max-h-[92vh] w-full overflow-y-auto rounded-t-lg border border-white/60 bg-white/90 shadow-soft backdrop-blur-md dark:border-slate-700/80 dark:bg-slate-950/90 sm:max-w-lg sm:rounded-lg">
        <header className="sticky top-0 z-10 flex items-start justify-between gap-3 border-b border-slate-200 bg-white/85 px-4 py-4 backdrop-blur-md dark:border-slate-800 dark:bg-slate-950/85">
          <div className="min-w-0">
            <p className="flex items-center gap-2 text-xs font-semibold text-brick dark:text-amber-300">
              <ShieldAlert className="h-4 w-4" aria-hidden="true" />
              {t("flag.eyebrow")}
            </p>
            <h2
              id={modalTitleId}
              className="mt-1 text-lg font-bold tracking-normal text-ink dark:text-slate-100"
            >
              {t("flag.title")}
            </h2>
            <p className="mt-1 truncate text-sm text-slate-500 dark:text-slate-400">
              {reviewTitle}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md text-slate-500 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800"
            aria-label={t("flag.close")}
          >
            <X className="h-5 w-5" aria-hidden="true" />
          </button>
        </header>

        <form className="grid gap-4 p-4" onSubmit={handleSubmit}>
          <div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-sm leading-6 text-amber-900 dark:border-amber-800/70 dark:bg-amber-950/40 dark:text-amber-200">
            <AlertTriangle className="mr-2 inline h-4 w-4 align-text-bottom" aria-hidden="true" />
            {t("flag.notice")}
          </div>

          <fieldset>
            <legend className="text-sm font-semibold text-slate-700 dark:text-slate-200">
              {t("flag.reason.label")}
            </legend>
            <div className="mt-2 grid gap-2">
              {(Object.keys(reasonKeys) as FlagReason[]).map((item) => (
                <label
                  key={item}
                  className={`flex cursor-pointer items-start gap-3 rounded-md border p-3 text-sm transition ${
                    reason === item
                      ? "border-campus bg-campus/10 text-ink dark:border-teal-300 dark:bg-teal-400/10 dark:text-slate-100"
                      : "border-slate-200 bg-white text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300 dark:hover:bg-slate-800"
                  }`}
                >
                  <input
                    type="radio"
                    name="flag-reason"
                    value={item}
                    checked={reason === item}
                    onChange={() => setReason(item)}
                    className="mt-1 h-4 w-4 accent-campus"
                  />
                  <span>{t(reasonKeys[item])}</span>
                </label>
              ))}
            </div>
          </fieldset>

          <label className="text-sm font-semibold text-slate-700 dark:text-slate-200">
            {t("flag.description.label")}
            <textarea
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              minLength={MIN_DESCRIPTION_LENGTH}
              required
              rows={5}
              placeholder={t("flag.description.placeholder")}
              className="mt-2 w-full resize-none rounded-md border border-slate-300 bg-white px-3 py-2 text-sm leading-6 text-ink outline-none transition focus:border-campus focus:ring-4 focus:ring-campus/15 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100 dark:placeholder:text-slate-500"
            />
          </label>
          <p
            className={`text-xs ${
              remainingCharacters > 0
                ? "text-brick dark:text-amber-300"
                : "text-emerald-700 dark:text-emerald-300"
            }`}
            aria-live="polite"
          >
            {remainingCharacters > 0
              ? t("flag.description.remaining", { count: remainingCharacters })
              : t("flag.description.ready")}
          </p>

          {errorMessage && (
            <p className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-950/40 dark:text-red-300">
              {errorMessage}
            </p>
          )}

          <div className="flex flex-col-reverse gap-2 border-t border-slate-200 pt-4 dark:border-slate-800 sm:flex-row sm:justify-end">
            <button
              type="button"
              onClick={onClose}
              className="h-10 rounded-md border border-slate-200 px-4 text-sm font-semibold text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
            >
              {t("flag.cancel")}
            </button>
            <button
              type="submit"
              disabled={!canSubmit}
              className="inline-flex h-10 items-center justify-center gap-2 rounded-md bg-brick px-4 text-sm font-semibold text-white transition hover:bg-brick/90 disabled:cursor-not-allowed disabled:bg-slate-300"
            >
              {isSubmitting ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              ) : (
                <Flag className="h-4 w-4" aria-hidden="true" />
              )}
              {t("flag.submit")}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
