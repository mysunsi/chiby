import { cn } from "@/lib/utils";

/** ACP ``PermissionOption`` 子集（与桥下发 ``permission_options`` 一致）。 */
export type HermesPermissionOptionItem = {
  optionId: string;
  name: string;
  kind?: string;
};

export interface HermesPermissionModalProps {
  open: boolean;
  permissionId: string;
  prompt: string;
  /** 空数组时使用与掌上AI大脑 ``acp_adapter/permissions`` 相同的默认三项。 */
  permissionOptions: HermesPermissionOptionItem[];
  onSelect: (optionId: string) => void;
  onDismiss?: () => void;
}

const DEFAULT_OPTIONS: HermesPermissionOptionItem[] = [
  { optionId: "allow_once", kind: "allow_once", name: "Allow once" },
  { optionId: "allow_always", kind: "allow_always", name: "Allow always" },
  { optionId: "deny", kind: "reject_once", name: "Deny" },
];

function isRejectOption(item: HermesPermissionOptionItem): boolean {
  const k = (item.kind || "").toLowerCase();
  if (k.startsWith("reject")) return true;
  return item.optionId.toLowerCase() === "deny";
}

export function HermesPermissionModal({
  open,
  permissionId,
  prompt,
  permissionOptions,
  onSelect,
  onDismiss,
}: HermesPermissionModalProps) {
  if (!open) return null;

  const opts = permissionOptions.length ? permissionOptions : DEFAULT_OPTIONS;

  return (
    <div
      className="ops-fixed ops-inset-0 ops-z-50 ops-flex ops-items-center ops-justify-center ops-bg-black/40 ops-p-4"
      role="dialog"
      aria-modal
      aria-labelledby="hermes-perm-title"
    >
      <div
        className={cn(
          "ops-max-h-[85vh] ops-w-full ops-max-w-md ops-overflow-hidden ops-rounded-lg ops-bg-white ops-shadow-xl",
        )}
      >
        <div className="ops-border-b ops-border-gray-100 ops-px-4 ops-py-3">
          <h2 id="hermes-perm-title" className="ops-text-sm ops-font-semibold ops-text-gray-900">
            Tool permission
          </h2>
          <p className="ops-mt-1 ops-font-mono ops-text-[10px] ops-text-gray-400">id: {permissionId}</p>
        </div>
        <div className="ops-max-h-[50vh] ops-overflow-y-auto ops-px-4 ops-py-3">
          <pre className="ops-whitespace-pre-wrap ops-break-words ops-text-xs ops-text-gray-800">{prompt}</pre>
        </div>
        <div className="ops-flex ops-flex-wrap ops-justify-end ops-gap-2 ops-border-t ops-border-gray-100 ops-bg-gray-50 ops-px-4 ops-py-3">
          {onDismiss ? (
            <button
              type="button"
              className="ops-rounded-md ops-px-3 ops-py-1.5 ops-text-sm ops-text-gray-600 hover:ops-bg-gray-200"
              onClick={onDismiss}
            >
              稍后
            </button>
          ) : null}
          {opts.map((item) => (
            <button
              key={item.optionId}
              type="button"
              onClick={() => onSelect(item.optionId)}
              className={cn(
                "ops-rounded-md ops-px-3 ops-py-1.5 ops-text-sm ops-font-medium",
                isRejectOption(item)
                  ? "ops-bg-red-600 ops-text-white hover:ops-bg-red-700"
                  : "ops-bg-blue-600 ops-text-white hover:ops-bg-blue-700",
              )}
            >
              {item.name}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
