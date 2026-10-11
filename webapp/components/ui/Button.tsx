"use client";

import type { ButtonHTMLAttributes } from "react";
import { cx } from "@/lib/cx";
import s from "./Button.module.css";

export type ButtonVariant = "default" | "primary" | "danger" | "ghost";

interface Props extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
}

export function Button({ variant = "default", className, type = "button", ...rest }: Props) {
  return (
    <button
      type={type}
      className={cx(s.btn, variant !== "default" && s[variant], className)}
      {...rest}
    />
  );
}
