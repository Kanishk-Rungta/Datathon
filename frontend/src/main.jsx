import React from 'react'
import { createRoot } from 'react-dom/client'
import App from './App.jsx'
import './styles/console.css'

createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
