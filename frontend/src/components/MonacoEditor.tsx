import MonacoEditor from "@monaco-editor/react";

interface Props {
  value: string;
  onChange: (value: string) => void;
}

export default function MonacoEditorWrapper({ value, onChange }: Props) {
  return (
    <MonacoEditor
      height="100%"
      defaultLanguage="markdown"
      theme="vs"
      value={value}
      onChange={(val) => onChange(val ?? "")}
      options={{
        minimap: { enabled: false },
        wordWrap: "on",
        fontSize: 14,
        lineNumbers: "on",
        scrollBeyondLastLine: false,
        padding: { top: 16, bottom: 16 },
        renderWhitespace: "none",
        tabSize: 2,
        automaticLayout: true,
        bracketPairColorization: { enabled: true },
        suggest: { showWords: false },
      }}
    />
  );
}
