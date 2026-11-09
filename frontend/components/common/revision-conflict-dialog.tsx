"use client";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";

interface RevisionConflictDialogProps {
  open: boolean;
  onReload(): void;
  subject: string;
}

/** Shown when the API rejects a change because the item changed after it was loaded. */
export function RevisionConflictDialog({ open, onReload, subject }: RevisionConflictDialogProps) {
  return (
    <AlertDialog open={open}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>This {subject} changed</AlertDialogTitle>
          <AlertDialogDescription>
            This {subject} was updated after you loaded it, so your change was not applied. Reload
            to see the latest version, then try again.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogAction onClick={onReload}>Reload latest</AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
