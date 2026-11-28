/** What the Studio piece shell (`routes/studio-piece.tsx`) passes to every tab. */
export interface PieceTabProps {
  /** The article (`art-…`) or standalone post (`post-…`) id from the URL. */
  pieceId: string;
}
