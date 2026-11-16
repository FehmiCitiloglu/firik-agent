import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { showError } from './store.js';

export function WorkspaceMarkdown({ text }: { text: string }) {
  return <div className="markdown"><ReactMarkdown remarkPlugins={[remarkGfm]} components={{
    a: ({ href, children }) => <a href={href} onClick={event => {
      event.preventDefault();
      if (href) void window.firik.openLink(href).catch(showError);
    }}>{children}</a>,
    img: ({ alt }) => <span className="muted">[Görsel{alt ? `: ${alt}` : ''}]</span>,
  }}>{text}</ReactMarkdown></div>;
}
