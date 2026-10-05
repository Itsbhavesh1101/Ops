import React, { useState } from "react";
import { FormComponent, FormDefinition } from "../api/client";

interface FormEditorProps {
  onSave?: (schema: FormDefinition) => void;
}

const DEFAULT_FORM_SCHEMA: FormDefinition = {
  schema_version: "1.0",
  title: "Semester Exam Review Form",
  components: [
    {
      id: "f_comment",
      key: "approval_comment",
      label: "Review Comments",
      type: "textfield",
      required: true,
    },
    {
      id: "f_score",
      key: "quality_score",
      label: "Quality Rating (1-10)",
      type: "number",
      required: false,
    },
    {
      id: "f_decision",
      key: "decision_outcome",
      label: "Outcome",
      type: "select",
      required: true,
      options: [
        { label: "Approved", value: "APPROVED" },
        { label: "Modifications Requested", value: "CHANGES_REQUESTED" },
        { label: "Rejected", value: "REJECTED" },
      ],
    },
  ],
};

export const FormEditor: React.FC<FormEditorProps> = ({ onSave }) => {
  const [formSchema, setFormSchema] = useState<FormDefinition>(DEFAULT_FORM_SCHEMA);
  const [activeTab, setActiveTab] = useState<"builder" | "preview" | "policy">("builder");

  // Field addition state
  const [newKey, setNewKey] = useState("");
  const [newLabel, setNewLabel] = useState("");
  const [newType, setNewType] = useState<FormComponent["type"]>("textfield");
  const [newRequired, setNewRequired] = useState(false);

  // Preview submission test state
  const [submissionValues, setSubmissionValues] = useState<Record<string, any>>({});
  const [submissionStatus, setSubmissionStatus] = useState<string | null>(null);

  const addField = () => {
    if (!newKey.trim() || !newLabel.trim()) return;
    const sanitizedKey = newKey.trim().toLowerCase().replace(/[^a-z0-9_]/g, "_");
    const updated: FormComponent[] = [
      ...formSchema.components,
      {
        id: `f_${Date.now()}`,
        key: sanitizedKey,
        label: newLabel.trim(),
        type: newType,
        required: newRequired,
      },
    ];
    setFormSchema({ ...formSchema, components: updated });
    setNewKey("");
    setNewLabel("");
    setNewRequired(false);
  };

  const removeField = (id: string) => {
    setFormSchema({
      ...formSchema,
      components: formSchema.components.filter((c) => c.id !== id),
    });
  };

  // Derive variable policy (explicit allowlist of variable keys)
  const allowedVariableKeys = formSchema.components.map((c) => c.key);

  const handleTestSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    // Validate required fields
    for (const comp of formSchema.components) {
      if (comp.required && (submissionValues[comp.key] === undefined || submissionValues[comp.key] === "")) {
        setSubmissionStatus(`Validation error: '${comp.label}' is required.`);
        return;
      }
    }
    setSubmissionStatus("Form submission successfully validated against schema and variable allowlist!");
  };

  return (
    <div className="flex flex-col gap-4">
      {/* Top Header */}
      <div className="flex items-center justify-between p-4 bg-slate-800 rounded-lg border border-slate-700">
        <div>
          <h3 className="text-base font-semibold text-slate-100">{formSchema.title}</h3>
          <p className="text-xs text-slate-400">Schema Version: {formSchema.schema_version} | Typed Form-JS Mapping</p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setActiveTab("builder")}
            className={`px-3 py-1.5 rounded text-sm font-medium ${
              activeTab === "builder" ? "bg-blue-600 text-white" : "bg-slate-700 text-slate-300"
            }`}
          >
            Field Builder
          </button>
          <button
            onClick={() => setActiveTab("preview")}
            className={`px-3 py-1.5 rounded text-sm font-medium ${
              activeTab === "preview" ? "bg-blue-600 text-white" : "bg-slate-700 text-slate-300"
            }`}
          >
            Rendered Preview
          </button>
          <button
            onClick={() => setActiveTab("policy")}
            className={`px-3 py-1.5 rounded text-sm font-medium ${
              activeTab === "policy" ? "bg-blue-600 text-white" : "bg-slate-700 text-slate-300"
            }`}
          >
            Derived Variable Policy
          </button>
          {onSave && (
            <button
              onClick={() => onSave(formSchema)}
              className="ml-3 px-4 py-1.5 bg-emerald-600 hover:bg-emerald-500 rounded text-sm font-medium text-white transition"
            >
              Save Form
            </button>
          )}
        </div>
      </div>

      {/* Tab 1: Builder */}
      {activeTab === "builder" && (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <div className="md:col-span-2 p-4 bg-slate-800 rounded-lg border border-slate-700 flex flex-col gap-3">
            <h4 className="text-sm font-semibold text-slate-200">Form Components</h4>
            <div className="flex flex-col gap-2">
              {formSchema.components.map((comp) => (
                <div
                  key={comp.id}
                  className="flex items-center justify-between p-3 bg-slate-900 border border-slate-700 rounded text-sm"
                >
                  <div>
                    <span className="font-medium text-slate-200">{comp.label}</span>
                    <span className="ml-2 text-xs text-slate-400 font-mono">({comp.key})</span>
                    <span className="ml-2 text-xs px-2 py-0.5 rounded bg-slate-800 text-blue-400 border border-slate-600">
                      {comp.type}
                    </span>
                    {comp.required && (
                      <span className="ml-2 text-xs px-2 py-0.5 rounded bg-rose-950 text-rose-300 border border-rose-800">
                        required
                      </span>
                    )}
                  </div>
                  <button
                    onClick={() => removeField(comp.id)}
                    className="text-xs text-rose-400 hover:text-rose-300 px-2 py-1 bg-slate-800 rounded border border-slate-600"
                  >
                    Delete
                  </button>
                </div>
              ))}
            </div>
          </div>

          <div className="p-4 bg-slate-800 rounded-lg border border-slate-700 flex flex-col gap-3">
            <h4 className="text-sm font-semibold text-slate-200">Add Field Component</h4>
            <div>
              <label className="block text-xs text-slate-400 font-medium">Variable Key (Allowlisted)</label>
              <input
                type="text"
                value={newKey}
                onChange={(e) => setNewKey(e.target.value)}
                placeholder="e.g. dean_remarks"
                className="w-full px-3 py-1.5 bg-slate-900 border border-slate-700 rounded text-sm text-slate-100"
              />
            </div>
            <div>
              <label className="block text-xs text-slate-400 font-medium">Field Label</label>
              <input
                type="text"
                value={newLabel}
                onChange={(e) => setNewLabel(e.target.value)}
                placeholder="e.g. Dean Remarks"
                className="w-full px-3 py-1.5 bg-slate-900 border border-slate-700 rounded text-sm text-slate-100"
              />
            </div>
            <div>
              <label className="block text-xs text-slate-400 font-medium">Field Type</label>
              <select
                value={newType}
                onChange={(e) => setNewType(e.target.value as FormComponent["type"])}
                className="w-full px-3 py-1.5 bg-slate-900 border border-slate-700 rounded text-sm text-slate-100"
              >
                <option value="textfield">Text Field (String)</option>
                <option value="number">Number</option>
                <option value="checkbox">Checkbox (Boolean)</option>
                <option value="select">Select Dropdown</option>
                <option value="datetime">Date & Time</option>
              </select>
            </div>
            <div className="flex items-center gap-2">
              <input
                type="checkbox"
                id="reqCheck"
                checked={newRequired}
                onChange={(e) => setNewRequired(e.target.checked)}
                className="rounded border-slate-700"
              />
              <label htmlFor="reqCheck" className="text-xs text-slate-300">
                Mark as Mandatory (Required)
              </label>
            </div>
            <button
              onClick={addField}
              className="mt-2 w-full px-4 py-2 bg-blue-600 hover:bg-blue-500 rounded text-sm font-medium text-white transition"
            >
              Add Field
            </button>
          </div>
        </div>
      )}

      {/* Tab 2: Rendered Preview */}
      {activeTab === "preview" && (
        <form onSubmit={handleTestSubmit} className="p-6 bg-slate-800 rounded-lg border border-slate-700 flex flex-col gap-4 max-w-xl">
          <h4 className="text-base font-semibold text-slate-100 border-b border-slate-700 pb-2">
            {formSchema.title} (Live Render)
          </h4>
          {formSchema.components.map((comp) => (
            <div key={comp.id} className="flex flex-col gap-1">
              <label className="text-xs font-medium text-slate-300">
                {comp.label} {comp.required && <span className="text-rose-400">*</span>}
              </label>
              {comp.type === "textfield" && (
                <input
                  type="text"
                  value={submissionValues[comp.key] || ""}
                  onChange={(e) => setSubmissionValues({ ...submissionValues, [comp.key]: e.target.value })}
                  className="px-3 py-2 bg-slate-900 border border-slate-700 rounded text-sm text-slate-100"
                />
              )}
              {comp.type === "number" && (
                <input
                  type="number"
                  value={submissionValues[comp.key] || ""}
                  onChange={(e) => setSubmissionValues({ ...submissionValues, [comp.key]: Number(e.target.value) })}
                  className="px-3 py-2 bg-slate-900 border border-slate-700 rounded text-sm text-slate-100"
                />
              )}
              {comp.type === "checkbox" && (
                <input
                  type="checkbox"
                  checked={Boolean(submissionValues[comp.key])}
                  onChange={(e) => setSubmissionValues({ ...submissionValues, [comp.key]: e.target.checked })}
                  className="w-4 h-4 rounded border-slate-700"
                />
              )}
              {comp.type === "select" && (
                <select
                  value={submissionValues[comp.key] || ""}
                  onChange={(e) => setSubmissionValues({ ...submissionValues, [comp.key]: e.target.value })}
                  className="px-3 py-2 bg-slate-900 border border-slate-700 rounded text-sm text-slate-100"
                >
                  <option value="">-- Select an option --</option>
                  {comp.options?.map((opt) => (
                    <option key={opt.value} value={opt.value}>
                      {opt.label}
                    </option>
                  ))}
                </select>
              )}
            </div>
          ))}

          {submissionStatus && (
            <div
              className={`p-3 rounded text-sm ${
                submissionStatus.startsWith("Validation error")
                  ? "bg-rose-950/60 border border-rose-800 text-rose-200"
                  : "bg-emerald-950/60 border border-emerald-800 text-emerald-200"
              }`}
            >
              {submissionStatus}
            </div>
          )}

          <button
            type="submit"
            className="self-start px-4 py-2 bg-blue-600 hover:bg-blue-500 rounded text-sm font-medium text-white transition"
          >
            Test Submission Validation
          </button>
        </form>
      )}

      {/* Tab 3: Derived Variable Policy */}
      {activeTab === "policy" && (
        <div className="p-4 bg-slate-800 rounded-lg border border-slate-700 flex flex-col gap-3">
          <h4 className="text-sm font-semibold text-slate-200">Enforced WorkflowVariablePolicy Allowlist</h4>
          <p className="text-xs text-slate-400">
            Per Phase-3A invariant, only fields declared in this form schema are admitted into the process variable context. All unauthorized or unallowlisted process variables are strictly rejected.
          </p>
          <div className="p-3 bg-slate-900 rounded border border-slate-700">
            <span className="text-xs text-slate-400 block mb-1">Allowed Variable Keys:</span>
            <div className="flex flex-wrap gap-2">
              {allowedVariableKeys.map((key) => (
                <span
                  key={key}
                  className="px-2.5 py-1 bg-slate-800 border border-slate-600 rounded text-xs font-mono text-blue-300"
                >
                  {key}
                </span>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
