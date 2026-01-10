/**
 * Toast - Base UI Toast component for error notifications
 *
 * Provides a toast manager for showing errors from anywhere in the app.
 * The ToastProvider wraps the app, and useToast hook provides toast methods.
 */

import { Toast } from '@base-ui-components/react/toast';
import { createContext, useContext, type ReactNode } from 'react';

interface ToastData {
  type?: 'error' | 'success';
}

// Create a global toast manager for external access
const toastManager = Toast.createToastManager();

// Context to expose toast methods
interface ToastContextValue {
  showError: (message: string) => void;
  showSuccess: (message: string) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext);
  if (!ctx) {
    throw new Error('useToast must be used within ToastProvider');
  }
  return ctx;
}

// Global function for vanilla TS integration
export function showErrorToast(message: string): void {
  toastManager.add({
    title: 'Error',
    description: message,
    data: { type: 'error' },
  });
}

export function showSuccessToast(message: string): void {
  toastManager.add({
    title: 'Success',
    description: message,
    data: { type: 'success' },
  });
}

// Toast list component - renders toasts from the provider context
function ToastList() {
  const manager = Toast.useToastManager();

  return (
    <>
      {manager.toasts.map((toast) => {
        const data = toast.data as ToastData | undefined;
        const typeClass = data?.type === 'error' ? 'toast-error' :
                         data?.type === 'success' ? 'toast-success' : '';

        return (
          <Toast.Root
            key={toast.id}
            toast={toast}
            className={`toast-root ${typeClass}`}
          >
            <Toast.Content className="toast-content">
              <Toast.Title className="toast-title" />
              <Toast.Description className="toast-description" />
            </Toast.Content>
            <Toast.Close className="toast-close">
              <CloseIcon />
            </Toast.Close>
          </Toast.Root>
        );
      })}
    </>
  );
}

interface ToastProviderProps {
  children: ReactNode;
}

export function ToastProvider({ children }: ToastProviderProps) {
  const contextValue: ToastContextValue = {
    showError: showErrorToast,
    showSuccess: showSuccessToast,
  };

  return (
    <ToastContext.Provider value={contextValue}>
      <Toast.Provider toastManager={toastManager} timeout={5000}>
        {children}
        <Toast.Portal>
          <Toast.Viewport className="toast-viewport">
            <ToastList />
          </Toast.Viewport>
        </Toast.Portal>
      </Toast.Provider>
    </ToastContext.Provider>
  );
}

function CloseIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="currentColor">
      <path d="M3.5 3.5L10.5 10.5M10.5 3.5L3.5 10.5" stroke="currentColor" strokeWidth="1.5" fill="none" />
    </svg>
  );
}

export default ToastProvider;
