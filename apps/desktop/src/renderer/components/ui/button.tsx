import type { ButtonHTMLAttributes } from 'react';
import { cva, type VariantProps } from 'class-variance-authority';
import { clsx } from 'clsx';
import { twMerge } from 'tailwind-merge';

const variants = cva('button', { variants: { variant: {
  default: 'button-primary', ghost: 'button-ghost', outline: 'button-outline',
} }, defaultVariants: { variant: 'default' } });

export function Button({ className, variant, ...props }: ButtonHTMLAttributes<HTMLButtonElement> & VariantProps<typeof variants>) {
  return <button className={twMerge(clsx(variants({ variant }), className))} {...props} />;
}
