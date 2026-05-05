'use client';

import { useState } from 'react';
import { Trash2, X, AlertTriangle, Loader2 } from 'lucide-react';

interface DeleteProductModalProps {
  productSlug: string;
  webSecret: string;
}

export function DeleteProductModal({ productSlug, webSecret }: DeleteProductModalProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [inputValue, setInputValue] = useState('');
  const [isDeleting, setIsDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleOpen = (e: React.MouseEvent) => {
    e.preventDefault();
    setIsOpen(true);
  };

  const handleClose = () => {
    setIsOpen(false);
    setInputValue('');
    setError(null);
  };

  const handleDelete = async () => {
    if (inputValue !== productSlug) return;
    
    setIsDeleting(true);
    setError(null);
    
    try {
      const res = await fetch(`/api/profile/${productSlug}`, {
        method: 'DELETE',
        headers: {
          'x-web-secret': webSecret,
        },
      });
      
      const data = await res.json();
      
      if (!res.ok || !data.ok) {
        throw new Error(data.error || 'Failed to delete product');
      }
      
      // Page will revalidate and card will disappear, we can just close the modal
      setIsOpen(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'An error occurred');
      setIsDeleting(false);
    }
  };

  return (
    <>
      <button
        onClick={handleOpen}
        className="p-1.5 text-gray-400 hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-950/30 rounded transition focus:outline-none focus:ring-2 focus:ring-red-500"
        title="Delete product"
        aria-label="Delete product"
      >
        <Trash2 className="w-4 h-4" />
      </button>

      {isOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm">
          <div 
            className="bg-white dark:bg-gray-900 rounded-xl shadow-xl w-full max-w-md overflow-hidden animate-in fade-in zoom-in-95 duration-200"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex justify-between items-center p-4 border-b border-gray-100 dark:border-gray-800">
              <div className="flex items-center text-red-600 dark:text-red-500">
                <AlertTriangle className="w-5 h-5 mr-2" />
                <h3 className="text-lg font-semibold">Delete Product</h3>
              </div>
              <button 
                onClick={handleClose}
                className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition"
              >
                <X className="w-5 h-5" />
              </button>
            </div>
            
            <div className="p-5">
              <p className="text-gray-600 dark:text-gray-300 mb-4 text-sm leading-relaxed">
                This action cannot be undone. This will permanently delete the profile, schedule, and all historical price tracking reports for <strong className="text-gray-900 dark:text-white">{productSlug}</strong>.
              </p>
              
              <div className="mb-4">
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1.5">
                  Please type <span className="font-mono font-bold select-all bg-gray-100 dark:bg-gray-800 px-1 rounded">{productSlug}</span> to confirm.
                </label>
                <input
                  type="text"
                  value={inputValue}
                  onChange={(e) => setInputValue(e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg focus:outline-none focus:ring-2 focus:ring-red-500 focus:border-red-500 bg-white dark:bg-gray-800 text-gray-900 dark:text-white font-mono text-sm"
                  placeholder={productSlug}
                  autoComplete="off"
                  disabled={isDeleting}
                />
              </div>

              {error && (
                <div className="p-3 mb-4 text-sm text-red-700 bg-red-50 dark:bg-red-900/30 dark:text-red-400 rounded-lg">
                  {error}
                </div>
              )}

              <div className="flex justify-end gap-3 mt-6">
                <button
                  onClick={handleClose}
                  disabled={isDeleting}
                  className="px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-lg hover:bg-gray-50 dark:bg-gray-800 dark:text-gray-300 dark:border-gray-700 dark:hover:bg-gray-700 focus:outline-none focus:ring-2 focus:ring-gray-500 transition"
                >
                  Cancel
                </button>
                <button
                  onClick={handleDelete}
                  disabled={inputValue !== productSlug || isDeleting}
                  className="flex items-center px-4 py-2 text-sm font-medium text-white bg-red-600 rounded-lg hover:bg-red-700 focus:outline-none focus:ring-2 focus:ring-red-500 disabled:opacity-50 disabled:cursor-not-allowed transition"
                >
                  {isDeleting && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
                  I understand, delete this product
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
