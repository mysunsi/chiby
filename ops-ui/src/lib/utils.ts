import { clsx, type ClassValue } from "clsx";
import { extendTailwindMerge } from "tailwind-merge";

/** prefix 须与 tailwind.config.js 的 prefix 一致；勿放在 override 内（会触发 TS 报错且运行时合并错误） */
const twMerge = extendTailwindMerge({
  prefix: "ops-",
});

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
