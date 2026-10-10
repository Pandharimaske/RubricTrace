import React from 'react'
import { useOutletContext } from 'react-router-dom'
import ReviewQueue from '../ReviewQueue'

/** The shared review queue, limited to this exam. */
export default function ExamReview() {
  const { exam } = useOutletContext()
  return <ReviewQueue examId={exam.exam_id} embedded />
}
