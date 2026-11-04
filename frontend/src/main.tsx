import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App'
import { ErrorBoundary } from './components/ErrorBoundary'

const hidePreloader = () => {
  const preloader = document.getElementById('preloader')
  if (preloader) {
    preloader.style.opacity = '0'
    setTimeout(() => {
      preloader.remove()
    }, 300)
  }
}

createRoot(document.getElementById('root')!).render(
  <ErrorBoundary>
    <App onReady={hidePreloader} />
  </ErrorBoundary>,
)