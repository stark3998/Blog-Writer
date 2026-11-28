/**
 * `DialectMarkdown`: the `@tiptap/markdown` extension, wired to
 * DialectMarkdownManager instead of the stock manager. Commands such as
 * `setContent(md, { contentType: 'markdown' })` and `editor.getMarkdown()`
 * keep working and now speak the article dialect.
 */
import { Markdown } from '@tiptap/markdown';

import { DialectMarkdownManager } from './manager';

export const DialectMarkdown = Markdown.extend({
  onBeforeCreate() {
    if (this.editor.markdown) {
      throw new Error('[article-dialect] the editor already has a markdown manager; add only one Markdown extension');
    }

    const manager = new DialectMarkdownManager({ extensions: this.editor.extensionManager.baseExtensions });
    this.storage.manager = manager;
    this.editor.markdown = manager;
    this.editor.getMarkdown = () => manager.serialize(this.editor.getJSON());

    const { content, contentType } = this.editor.options;
    if (contentType === 'markdown' && typeof content === 'string') {
      const json = manager.parse(content);
      if (json.content?.length) {
        this.editor.options.content = json;
      }
    }
  },
});
